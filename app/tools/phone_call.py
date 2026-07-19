"""电话外呼工具 - Agent可主动给用户打电话

当用户请求电话沟通时，Agent可调用此工具发起外呼。
实际外呼需要FreeSWITCH，无FreeSWITCH时返回模拟结果。
"""

from langchain_core.tools import tool


@tool
async def place_phone_call(
    phone_number: str,
    reason: str = "",
    user_id: str = "",
) -> str:
    """给用户拨打电话，AI客服将通过电话与用户沟通。

    当用户明确要求电话沟通、或问题复杂需要语音交流时使用。

    Args:
        phone_number: 用户手机号码，11位数字
        reason: 外呼原因(简述)
        user_id: 用户ID(可选，用于关联历史会话)
    """
    # 验证手机号格式
    if not phone_number or len(phone_number) != 11 or not phone_number.isdigit():
        return "手机号格式不正确，请提供11位手机号码。"

    if not phone_number.startswith("1"):
        return "手机号格式不正确，应以1开头。"

    # PII脱敏: 日志和返回值中不暴露完整手机号
    from app.core.auth import mask_phone
    masked_phone = mask_phone(phone_number)

    try:
        from app.api.deps import get_outbound_manager

        manager = await get_outbound_manager()
        call = await manager.place_call(
            phone_number=phone_number,  # 实际调用仍传完整号码
            user_id=user_id or None,
            metadata={"reason": reason},
        )

        if call.status.value == "failed":
            return f"外呼失败: {call.failure_reason or '未知原因'}，建议转接人工客服处理。"

        return (
            f"已成功发起外呼，呼叫ID: {call.call_id}，"
            f"被叫号码: {masked_phone}，"  # 返回值用脱敏号码
            f"当前状态: {call.status.value}。"
            f"用户接听后AI将自动开始对话。"
        )

    except Exception as e:
        return f"外呼系统暂时不可用({str(e)})，建议转接人工客服处理。"


# 工具列表(供graph.py注册)
PHONE_CALL_TOOLS = [place_phone_call]
