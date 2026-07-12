"""Agent结构化输出Schema - 所有LLM输出的Pydantic模型定义

核心原则:
1. 所有关键LLM输出必须对应一个Pydantic模型
2. 字段用Field定义约束和描述，同时用于生成Prompt指令
3. 验证失败时降级而非崩溃，保证服务可用性
4. 四层降级链: with_structured_output → tool_calling → bind_tools手动 → JSON解析

降级策略:
- Layer 1: with_structured_output(默认method) — 利用模型原生结构化能力
- Layer 2: with_structured_output(method="tool_calling") — 显式使用tool calling策略
- Layer 3: 手动bind_tools + 解析tool_calls — 将schema转为"假工具"让模型调用
- Layer 4: schema_to_json_instruction + safe_parse_model — Prompt要求JSON+手动解析

模型分类:
- IntentClassification: 意图分类结果
- ConversationSummary: 对话摘要结构化输出
- FactExtraction: 关键事实提取
- AgentResponseMeta: 最终回复元数据(情感、建议动作)
"""

from typing import Literal, Optional
from pydantic import BaseModel, Field


# ============================================================
# 意图分类
# ============================================================

class IntentClassification(BaseModel):
    """意图分类结构化输出

    用于 intent_router 节点，LLM必须输出符合此结构的结果。
    """
    intent: Literal[
        "order_query",
        "product_search",
        "refund_service",
        "knowledge_faq",
        "human_escalation",
        "general_chat",
    ] = Field(
        description="用户意图分类",
    )

    sentiment: Literal["positive", "neutral", "negative", "angry"] = Field(
        default="neutral",
        description="用户情感分类: positive(满意)/neutral(中性)/negative(不满)/angry(愤怒)",
    )

    sentiment_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="情感强度分数，0.0-1.0，angry时通常>0.8应自动转人工",
    )


# ============================================================
# 对话摘要
# ============================================================

class ConversationSummary(BaseModel):
    """对话摘要结构化输出

    用于 summarize 节点，确保摘要包含所有关键信息。
    """
    summary_text: str = Field(
        description="对话摘要正文，保留关键事实(订单号/金额/商品名等)，不超过300字",
    )

    key_topics: list[str] = Field(
        default_factory=list,
        description="本次对话涉及的主要话题，如['订单查询', '物流追踪']",
    )

    key_facts: dict[str, str] = Field(
        default_factory=dict,
        description="从对话中提取的关键事实键值对，如{'order_id': 'ORD20250101001', 'logistics_status': '配送中'}",
    )

    sentiment_trend: Literal["improving", "stable", "deteriorating"] = Field(
        default="stable",
        description="用户情感趋势: improving(好转)/stable(稳定)/deteriorating(恶化)",
    )

    resolved: bool = Field(
        default=False,
        description="用户问题是否已被解决",
    )


# ============================================================
# 关键事实提取
# ============================================================

class FactExtraction(BaseModel):
    """从对话中提取的关键事实

    用于用户画像更新，从消息中提取结构化事实。
    """
    order_ids: list[str] = Field(
        default_factory=list,
        description="对话中提到的订单号列表，如['ORD20250101001']",
    )

    product_names: list[str] = Field(
        default_factory=list,
        description="对话中提到的商品名称，如['智能手表', '无线耳机']",
    )

    phone_numbers: list[str] = Field(
        default_factory=list,
        description="对话中提到的手机号码",
    )

    amounts: list[str] = Field(
        default_factory=list,
        description="对话中提到的金额，如['299.00', '159.00']",
    )

    complaint_reason: Optional[str] = Field(
        default=None,
        description="如果用户投诉，提取投诉原因，如'商品质量问题'",
    )


# ============================================================
# 最终回复元数据
# ============================================================

class AgentResponseMeta(BaseModel):
    """最终回复的元数据

    在 response 节点生成回复后，提取元数据用于记录和后续分析。
    注意: 回复正文本身是自由文本，不需要结构化。
    """
    response_type: Literal[
        "direct_answer",
        "tool_result_summary",
        "clarification_needed",
        "escalation_notice",
        "general_chat",
    ] = Field(
        description="回复类型: direct_answer(直接回答)/tool_result_summary(工具结果整合)/clarification_needed(需要追问)/escalation_notice(转人工通知)/general_chat(闲聊)",
    )

    confidence: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="回复置信度，低于0.5时建议转人工",
    )

    suggested_actions: list[str] = Field(
        default_factory=list,
        description="建议用户下一步操作，如['查看物流详情', '申请退款']",
    )

    needs_followup: bool = Field(
        default=False,
        description="是否需要后续跟进(如退款需要1-3天到账)",
    )


# ============================================================
# 工具函数
# ============================================================

def schema_to_json_instruction(model_class: type[BaseModel]) -> str:
    """将Pydantic模型转为LLM可理解的JSON输出指令

    自动从Field的description和约束生成格式说明，
    确保LLM输出的JSON能被Pydantic正确解析。

    Args:
        model_class: Pydantic模型类

    Returns:
        格式指令文本，可直接追加到Prompt中
    """
    schema = model_class.model_json_schema()
    properties = schema.get("properties", {})

    # 构建JSON格式示例
    example = {}
    field_descriptions = []

    for field_name, field_info in properties.items():
        # 生成示例值
        if "enum" in field_info:
            example[field_name] = field_info["enum"][0]
        elif field_info.get("type") == "number":
            example[field_name] = 0.5
        elif field_info.get("type") == "boolean":
            example[field_name] = False
        elif field_info.get("type") == "array":
            example[field_name] = []
        elif field_info.get("type") == "object":
            example[field_name] = {}
        else:
            example[field_name] = ""

        # 收集字段描述
        desc = field_info.get("description", "")
        if "enum" in field_info:
            desc += f" (可选值: {', '.join(field_info['enum'])})"
        if field_name in schema.get("required", []):
            desc += " [必填]"
        field_descriptions.append(f"  - {field_name}: {desc}")

    import json
    example_json = json.dumps(example, ensure_ascii=False, indent=2)

    instruction = f"""请严格按照以下JSON格式输出结果，不要输出任何其他内容（不要markdown标记、不要解释）：

{example_json}

字段说明:
{chr(10).join(field_descriptions)}"""

    return instruction


def safe_parse_model(model_class: type[BaseModel], text: str) -> Optional[BaseModel]:
    """安全解析LLM输出为Pydantic模型

    多层容错:
    1. 直接解析
    2. 提取```json```包裹内容
    3. 提取{}包裹内容
    4. 解析失败返回None

    Args:
        model_class: 目标Pydantic模型类
        text: LLM原始输出文本

    Returns:
        解析成功的模型实例，或None
    """
    import json
    import re

    # 尝试1: 直接解析
    try:
        data = json.loads(text.strip())
        return model_class(**data)
    except (json.JSONDecodeError, Exception):
        pass

    # 尝试2: 提取```json```包裹内容
    json_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if json_match:
        try:
            data = json.loads(json_match.group(1).strip())
            return model_class(**data)
        except (json.JSONDecodeError, Exception):
            pass

    # 尝试3: 提取{}包裹内容
    brace_match = re.search(r"\{[\s\S]*\}", text)
    if brace_match:
        try:
            data = json.loads(brace_match.group(0))
            return model_class(**data)
        except (json.JSONDecodeError, Exception):
            pass

    return None


async def structured_llm_output(
    model_class: type[BaseModel],
    llm,
    prompt_template=None,
    prompt_input: dict | None = None,
    messages: list | None = None,
    config: dict | None = None,
    semaphore=None,
    per_layer_timeout: float = 8.0,
) -> Optional[BaseModel]:
    """统一的结构化输出函数 — 四层降级链

    将Pydantic模型转为LLM可理解的结构化输出，按可靠性从高到低尝试:

    Layer 1: with_structured_output(默认method)
        → 利用模型原生的function_calling/json_schema能力，直接返回Pydantic实例

    Layer 2: with_structured_output(method="tool_calling")
        → 显式指定tool_calling策略，兼容支持tools但不支持functions的API(如智谱)

    Layer 3: 手动bind_tools + 解析tool_calls
        → 将schema转为"假工具"，让模型以tool call形式输出结构化数据
        → 这是最可靠的方式，因为所有支持tool calling的模型都能工作

    Layer 4: schema_to_json_instruction + safe_parse_model
        → Prompt要求JSON输出 + 多层容错手动解析
        → 兜底方案，依赖模型的指令遵循能力

    Args:
        model_class: Pydantic模型类(如IntentClassification)
        llm: LangChain LLM实例(原始，不带prompt管道)
        prompt_template: ChatPromptTemplate实例(可选，与prompt_input配合使用)
        prompt_input: ChatPromptTemplate的变量字典(需要配合prompt_template)
        messages: 直接传入的message列表(与prompt_input二选一)
        config: 传给llm.ainvoke的config(如tags)
        semaphore: asyncio.Semaphore，控制并发
        per_layer_timeout: 每层超时秒数(默认8秒，防止降级链总耗时过长)

    Returns:
        解析成功的Pydantic模型实例，或None(所有层都失败)

    Usage:
        # 方式1: 使用prompt_template + prompt_input
        result = await structured_llm_output(
            IntentClassification, llm,
            prompt_template=INTENT_ROUTER_PROMPT,
            prompt_input={"memory_context": "...", "history": [...]},
        )

        # 方式2: 直接传入messages
        result = await structured_llm_output(
            ConversationSummary, llm,
            messages=[SystemMessage(...), HumanMessage(...)],
        )
    """
    import asyncio
    import time
    import logging
    from langchain_core.messages import SystemMessage, HumanMessage

    logger = logging.getLogger(__name__)
    schema_name = model_class.__name__

    async def _invoke(runnable, input_data, cfg=None):
        """带信号量控制+超时的invoke"""
        async def _do_invoke():
            if semaphore:
                async with semaphore:
                    return await runnable.ainvoke(input_data, config=cfg)
            return await runnable.ainvoke(input_data, config=cfg)

        return await asyncio.wait_for(_do_invoke(), timeout=per_layer_timeout)

    # 统一输入: 确定最终要传给LLM的输入数据
    if prompt_template is not None and prompt_input is not None:
        # 有ChatPromptTemplate: 先格式化为messages，再传给各层
        formatted_messages = prompt_template.format_messages(**prompt_input)
    elif messages is not None:
        formatted_messages = list(messages)
    else:
        logger.warning("structured_llm_output: 需要提供(prompt_template+prompt_input)或messages")
        return None

    cfg = config or {}
    skip_layer2 = False  # Layer1抛NotImplementedError时跳过Layer2

    # ============================================================
    # Layer 1: with_structured_output(默认method)
    # ============================================================
    t0 = time.monotonic()
    try:
        structured_llm = llm.with_structured_output(model_class)
        result = await _invoke(structured_llm, formatted_messages, cfg)

        if result is not None and isinstance(result, model_class):
            logger.debug(
                "%s: Layer1成功 (%.1fs)", schema_name, time.monotonic() - t0,
            )
            return result
        elif result is not None:
            # 返回了结果但类型不对，尝试转换
            try:
                return model_class(**result) if isinstance(result, dict) else result
            except Exception:
                pass
    except NotImplementedError as e:
        # NotImplementedError → 模型不支持structured_output，Layer2同样会失败
        skip_layer2 = True
        logger.debug(
            "%s: Layer1 NotImplError→跳Layer2 (%.1fs): %s",
            schema_name, time.monotonic() - t0, str(e)[:100],
        )
    except asyncio.TimeoutError:
        logger.debug(
            "%s: Layer1超时(%.1fs > %.1fs)", schema_name, time.monotonic() - t0, per_layer_timeout,
        )
    except (TypeError, ValueError, Exception) as e:
        logger.debug(
            "%s: Layer1失败 (%.1fs): %s: %s",
            schema_name, time.monotonic() - t0, type(e).__name__, str(e)[:100],
        )

    # ============================================================
    # Layer 2: with_structured_output(method="tool_calling")
    # 如果Layer1抛NotImplementedError则跳过(对智谱API两种method等价)
    # ============================================================
    if skip_layer2:
        logger.debug("%s: 跳过Layer2(与Layer1等价)", schema_name)
    else:
        t0 = time.monotonic()
        try:
            structured_llm = llm.with_structured_output(model_class, method="tool_calling")
            result = await _invoke(structured_llm, formatted_messages, cfg)

            if result is not None and isinstance(result, model_class):
                logger.debug(
                    "%s: Layer2成功 (%.1fs)", schema_name, time.monotonic() - t0,
                )
                return result
            elif result is not None:
                try:
                    return model_class(**result) if isinstance(result, dict) else result
                except Exception:
                    pass
        except asyncio.TimeoutError:
            logger.debug(
                "%s: Layer2超时(%.1fs > %.1fs)", schema_name, time.monotonic() - t0, per_layer_timeout,
            )
        except (NotImplementedError, TypeError, ValueError, Exception) as e:
            logger.debug(
                "%s: Layer2失败 (%.1fs): %s: %s",
                schema_name, time.monotonic() - t0, type(e).__name__, str(e)[:100],
            )

    # ============================================================
    # Layer 3: 手动bind_tools + 解析tool_calls
    # 将Pydantic schema转为"假工具"，让模型以tool call形式输出
    # ============================================================
    t0 = time.monotonic()
    try:
        tool_schema = _pydantic_to_tool_schema(model_class)
        bound_llm = llm.bind_tools(
            [tool_schema],
            tool_choice={"type": "function", "function": {"name": schema_name}},
        )
        response = await _invoke(bound_llm, formatted_messages, cfg)

        # 解析tool_calls
        if hasattr(response, "tool_calls") and response.tool_calls:
            tool_call = response.tool_calls[0]
            args = tool_call.get("args", {})
            if args:
                result = model_class(**args)
                logger.debug(
                    "%s: Layer3 bind_tools成功 (%.1fs)", schema_name, time.monotonic() - t0,
                )
                return result

        # 如果没有tool_calls，尝试从content解析
        if hasattr(response, "content") and response.content:
            parsed = safe_parse_model(model_class, response.content)
            if parsed:
                logger.debug(
                    "%s: Layer3 content解析成功 (%.1fs)", schema_name, time.monotonic() - t0,
                )
                return parsed

    except asyncio.TimeoutError:
        logger.debug(
            "%s: Layer3超时(%.1fs > %.1fs)", schema_name, time.monotonic() - t0, per_layer_timeout,
        )
    except Exception as e:
        logger.debug(
            "%s: Layer3失败 (%.1fs): %s: %s",
            schema_name, time.monotonic() - t0, type(e).__name__, str(e)[:100],
        )

    # ============================================================
    # Layer 4: JSON Prompt + 手动解析 (最后兜底)
    # ============================================================
    t0 = time.monotonic()
    try:
        json_instruction = schema_to_json_instruction(model_class)
        # 在原始messages前插入JSON格式指令
        json_messages = [SystemMessage(content=json_instruction)] + formatted_messages
        response = await _invoke(llm, json_messages, cfg)
        if hasattr(response, "content"):
            parsed = safe_parse_model(model_class, response.content)
            if parsed:
                logger.debug(
                    "%s: Layer4 JSON解析成功 (%.1fs)", schema_name, time.monotonic() - t0,
                )
                return parsed

    except asyncio.TimeoutError:
        logger.debug(
            "%s: Layer4超时(%.1fs > %.1fs)", schema_name, time.monotonic() - t0, per_layer_timeout,
        )
    except Exception as e:
        logger.warning("%s: Layer4 JSON解析失败: %s", schema_name, e)

    logger.warning("%s: 所有4层降级均失败", schema_name)
    return None


def _pydantic_to_tool_schema(model_class: type[BaseModel]) -> dict:
    """将Pydantic模型转为OpenAI工具调用格式的schema

    这是Layer 3的关键步骤：把Pydantic模型"伪装"成一个工具，
    让LLM以tool call的形式输出结构化数据。

    Args:
        model_class: Pydantic模型类

    Returns:
        符合OpenAI function calling格式的工具schema字典
    """
    schema = model_class.model_json_schema()

    # 提取描述(优先使用模型docstring)
    description = model_class.__doc__ or schema.get("description", f"Output {model_class.__name__}")

    # 清理schema中Pydantic添加的额外字段
    properties = schema.get("properties", {})
    cleaned_props = {}
    required_fields = schema.get("required", [])

    for field_name, field_info in properties.items():
        prop = dict(field_info)
        # 移除Pydantic的额外字段(default, examples等)
        prop.pop("default", None)
        prop.pop("examples", None)
        # 保留title(如果有)和description
        cleaned_props[field_name] = prop

    return {
        "type": "function",
        "function": {
            "name": model_class.__name__,
            "description": description.strip(),
            "parameters": {
                "type": "object",
                "properties": cleaned_props,
                "required": required_fields,
            },
        },
    }
