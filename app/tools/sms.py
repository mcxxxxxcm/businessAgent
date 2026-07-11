"""短信发送工具 - 支持阿里云/腾讯云/模拟模式

短信场景:
1. 订单通知: 发货提醒、物流更新、签收通知
2. 退款通知: 退款受理、退款到账
3. 验证码: 用户身份验证
4. 人工客服回访: 约定时间短信提醒

接入流程:
- 阿里云: 注册 → 企业认证 → 开通短信服务 → 申请签名和模板 → 获取AccessKey
- 腾讯云: 注册 → 企业认证 → 开通短信服务 → 申请签名和模板 → 获取SecretId/Key
- 模拟模式: 无需任何配置，短信内容只打印到日志（开发测试用）

重要: 阿里云/腾讯云SDK为同步调用，通过 asyncio.to_thread 在线程池中执行，
     避免阻塞asyncio事件循环。
"""

import asyncio
import json
import logging
from typing import Optional

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

# SMS调用超时(秒)
SMS_TIMEOUT_SECONDS = 10


# ============================================================
# SDK Client 单例 (懒初始化，线程安全)
# ============================================================

_aliyun_client = None
_tencent_client = None


def _get_aliyun_client():
    """获取阿里云SMS Client单例(线程安全，SDK内部有锁)"""
    global _aliyun_client
    if _aliyun_client is not None:
        return _aliyun_client

    from app.core.config import settings
    from alibabacloud_dysmsapi20170525.client import Client
    from alibabacloud_tea_openapi import models as open_api_models

    config = open_api_models.Config(
        access_key_id=settings.ALIYUN_ACCESS_KEY_ID,
        access_key_secret=settings.ALIYUN_ACCESS_KEY_SECRET,
        endpoint="dysmsapi.aliyuncs.com",
    )
    _aliyun_client = Client(config)
    return _aliyun_client


def _get_tencent_client():
    """获取腾讯云SMS Client单例(线程安全)"""
    global _tencent_client
    if _tencent_client is not None:
        return _tencent_client

    from app.core.config import settings
    from tencentcloud.sms.v20210111 import sms_client
    from tencentcloud.common import credential
    from tencentcloud.common.profile.client_profile import ClientProfile
    from tencentcloud.common.profile.http_profile import HttpProfile

    cred = credential.Credential(settings.TENCENT_SECRET_ID, settings.TENCENT_SECRET_KEY)
    http_profile = HttpProfile(endpoint="sms.tencentcloudapi.com")
    client_profile = ClientProfile(httpProfile=http_profile)
    _tencent_client = sms_client.SmsClient(cred, "ap-guangzhou", client_profile)
    return _tencent_client


# ============================================================
# 同步发送函数 (在线程池中执行)
# ============================================================

def _send_sms_aliyun_sync(
    phone_number: str,
    template_code: str,
    template_params: dict,
    sign_name: str,
) -> dict:
    """阿里云同步发送(在线程池中运行，不阻塞事件循环)"""
    try:
        client = _get_aliyun_client()
        from alibabacloud_dysmsapi20170525 import models as sms_models

        request = sms_models.SendSmsRequest(
            phone_numbers=phone_number,
            sign_name=sign_name,
            template_code=template_code,
            template_param=json.dumps(template_params, ensure_ascii=False),
        )

        response = client.send_sms(request)

        if response.body.code == "OK":
            logger.info("阿里云短信发送成功: phone=%s, template=%s", phone_number, template_code)
            return {"success": True, "biz_id": response.body.biz_id}
        else:
            logger.warning(
                "阿里云短信发送失败: phone=%s, code=%s, msg=%s",
                phone_number, response.body.code, response.body.message,
            )
            return {"success": False, "error": f"{response.body.code}: {response.body.message}"}

    except ImportError:
        return {"success": False, "error": "阿里云SDK未安装，请运行: pip install alibabacloud-dysmsapi20170525"}
    except Exception as e:
        logger.error("阿里云短信异常: %s", e)
        return {"success": False, "error": str(e)}


def _send_sms_tencent_sync(
    phone_number: str,
    template_code: str,
    template_params: dict,
    sign_name: str,
) -> dict:
    """腾讯云同步发送(在线程池中运行，不阻塞事件循环)"""
    try:
        client = _get_tencent_client()
        from tencentcloud.sms.v20210111 import models as sms_models
        from app.core.config import settings

        # 腾讯云手机号格式: +8617628876799
        phone_with_prefix = f"+86{phone_number}"

        request = sms_models.SendSmsRequest()
        request.SmsSdkAppId = settings.TENCENT_SMS_APP_ID
        request.SignName = sign_name
        request.TemplateId = template_code
        request.TemplateParamSet = [str(v) for v in template_params.values()]
        request.PhoneNumberSet = [phone_with_prefix]

        response = client.SendSms(request)

        status = response.SendStatusSet[0]
        if status.Code == "Ok":
            logger.info("腾讯云短信发送成功: phone=%s, template=%s", phone_number, template_code)
            return {"success": True, "biz_id": status.SerialNo}
        else:
            logger.warning("腾讯云短信发送失败: %s", status.Message)
            return {"success": False, "error": f"{status.Code}: {status.Message}"}

    except ImportError:
        return {"success": False, "error": "腾讯云SDK未安装，请运行: pip install tencentcloud-sdk-python-sms"}
    except Exception as e:
        logger.error("腾讯云短信异常: %s", e)
        return {"success": False, "error": str(e)}


# ============================================================
# 异步包装层 (不阻塞事件循环)
# ============================================================

async def _send_sms_aliyun(
    phone_number: str,
    template_code: str,
    template_params: dict,
    sign_name: str,
) -> dict:
    """通过asyncio.to_thread在线程池中执行阿里云同步SDK调用"""
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(
                _send_sms_aliyun_sync,
                phone_number, template_code, template_params, sign_name,
            ),
            timeout=SMS_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.error("阿里云短信发送超时(%ds): phone=%s", SMS_TIMEOUT_SECONDS, phone_number)
        return {"success": False, "error": f"发送超时({SMS_TIMEOUT_SECONDS}秒)"}


async def _send_sms_tencent(
    phone_number: str,
    template_code: str,
    template_params: dict,
    sign_name: str,
) -> dict:
    """通过asyncio.to_thread在线程池中执行腾讯云同步SDK调用"""
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(
                _send_sms_tencent_sync,
                phone_number, template_code, template_params, sign_name,
            ),
            timeout=SMS_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.error("腾讯云短信发送超时(%ds): phone=%s", SMS_TIMEOUT_SECONDS, phone_number)
        return {"success": False, "error": f"发送超时({SMS_TIMEOUT_SECONDS}秒)"}


async def _send_sms_dummy(
    phone_number: str,
    template_code: str,
    template_params: dict,
    sign_name: str,
) -> dict:
    """模拟短信发送（开发测试用）

    短信内容只打印到日志，不实际发送。
    """
    # 模拟模板内容渲染
    template_map = {
        "SMS_ORDER_SHIP": "您的订单{order_id}已发货，预计{date}送达。",
        "SMS_ORDER_DELIVERED": "您的订单{order_id}已签收，感谢您的购买！",
        "SMS_REFUND_SUCCESS": "您的退款{amount}元已到账，请查收。",
        "SMS_REFUND_PROCESSING": "您的退款申请已受理，预计{days}个工作日到账。",
        "SMS_VERIFICATION_CODE": "您的验证码为{code}，{exp}分钟内有效。",
        "SMS_CALLBACK_NOTICE": "尊敬的客户，您预约的回访时间为{time}，届时将有客服与您联系。",
    }

    template_text = template_map.get(template_code, str(template_params))

    # 简单模板渲染
    try:
        content = template_text.format(**template_params)
    except (KeyError, IndexError):
        content = f"[模板{template_code}] 参数: {template_params}"

    logger.info(
        "📩 模拟短信发送 | 签名:【%s】 | 手机: %s | 内容: %s",
        sign_name, phone_number, content,
    )

    return {"success": True, "mode": "dummy", "content": content}


async def send_sms(
    phone_number: str,
    template_code: str,
    template_params: Optional[dict] = None,
    sign_name: Optional[str] = None,
) -> dict:
    """短信发送统一入口

    根据 SMS_PROVIDER 配置自动选择发送渠道:
    - aliyun: 阿里云短信
    - tencent: 腾讯云短信
    - dummy: 模拟模式(日志打印)

    Args:
        phone_number: 手机号(11位)
        template_code: 短信模板ID(需在平台提前创建并审核)
        template_params: 模板参数 dict，如 {"order_id": "ORD123", "date": "1月5日"}
        sign_name: 短信签名，默认使用配置中的 SMS_SIGN_NAME

    Returns:
        {"success": bool, "error": str(失败时), ...}
    """
    from app.core.config import settings

    template_params = template_params or {}
    sign_name = sign_name or settings.SMS_SIGN_NAME

    # 验证手机号
    if not phone_number or len(phone_number) != 11 or not phone_number.isdigit():
        return {"success": False, "error": "手机号格式不正确，需为11位数字"}
    if not phone_number.startswith("1"):
        return {"success": False, "error": "手机号格式不正确，应以1开头"}

    # dry_run模式: 即使配了真实渠道也不实际发送
    if settings.SMS_DRY_RUN:
        return await _send_sms_dummy(phone_number, template_code, template_params, sign_name)

    # 按配置选择渠道
    provider = settings.SMS_PROVIDER
    if provider == "aliyun":
        if not settings.ALIYUN_ACCESS_KEY_ID:
            return {"success": False, "error": "阿里云AccessKey未配置"}
        return await _send_sms_aliyun(phone_number, template_code, template_params, sign_name)

    elif provider == "tencent":
        if not settings.TENCENT_SECRET_ID:
            return {"success": False, "error": "腾讯云SecretId未配置"}
        return await _send_sms_tencent(phone_number, template_code, template_params, sign_name)

    else:
        return await _send_sms_dummy(phone_number, template_code, template_params, sign_name)


# ============================================================
# Agent工具定义
# ============================================================

@tool
async def send_order_notification(
    phone_number: str,
    order_id: str,
    status: str,
    date: str = "",
) -> str:
    """发送订单状态通知短信给用户。

    适用于: 订单发货、签收、物流更新等场景。

    Args:
        phone_number: 用户手机号(11位)
        order_id: 订单号
        status: 订单状态，如"已发货"、"已签收"、"配送中"
        date: 预计送达日期或时间(可选)
    """
    # 根据状态选择模板
    if "发货" in status:
        template_code = "SMS_ORDER_SHIP"
    elif "签收" in status:
        template_code = "SMS_ORDER_DELIVERED"
    else:
        template_code = "SMS_ORDER_SHIP"

    result = await send_sms(
        phone_number=phone_number,
        template_code=template_code,
        template_params={"order_id": order_id, "date": date or "近日"},
    )

    if result["success"]:
        return f"已向{phone_number}发送订单{order_id}的{status}通知短信。"
    return f"短信发送失败: {result.get('error', '未知原因')}"


@tool
async def send_refund_notification(
    phone_number: str,
    order_id: str,
    amount: str,
    status: str = "已到账",
) -> str:
    """发送退款通知短信给用户。

    适用于: 退款受理、退款到账等场景。

    Args:
        phone_number: 用户手机号(11位)
        order_id: 订单号
        amount: 退款金额(如"299.00")
        status: 退款状态，如"已到账"、"处理中"
    """
    if "到账" in status:
        template_code = "SMS_REFUND_SUCCESS"
    else:
        template_code = "SMS_REFUND_PROCESSING"

    result = await send_sms(
        phone_number=phone_number,
        template_code=template_code,
        template_params={"order_id": order_id, "amount": amount, "days": "3-5"},
    )

    if result["success"]:
        return f"已向{phone_number}发送订单{order_id}退款{amount}元的{status}通知短信。"
    return f"短信发送失败: {result.get('error', '未知原因')}"


@tool
async def send_custom_sms(
    phone_number: str,
    content: str,
    reason: str = "",
) -> str:
    """发送自定义内容短信给用户。

    当没有匹配的预设模板时使用。生产环境中需要提前在短信平台审核模板。
    开发模式下将直接打印到日志。

    Args:
        phone_number: 用户手机号(11位)
        content: 短信内容(简洁明了，不超过70字)
        reason: 发送原因(内部记录，不发送给用户)
    """
    result = await send_sms(
        phone_number=phone_number,
        template_code="CUSTOM",
        template_params={"content": content},
    )

    if result["success"]:
        mode = result.get("mode", "")
        msg = f"已向{phone_number}发送短信。"
        if mode == "dummy":
            msg += f"(模拟模式，内容: {content})"
        return msg
    return f"短信发送失败: {result.get('error', '未知原因')}"


# 工具列表(供graph.py注册)
SMS_TOOLS = [send_order_notification, send_refund_notification, send_custom_sms]
