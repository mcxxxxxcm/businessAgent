"""Direct graph test with detailed orchestrator logging"""
import asyncio
import sys
import time
import logging

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Enable all logs from task_orchestrator
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')

async def main():
    from langchain_core.messages import HumanMessage
    from app.api.deps import get_graph
    from app.agent.graph import HIGH_RISK_TOOL_NODES

    graph = await get_graph()
    input_data = {
        "messages": [HumanMessage(content="帮我退款订单为123456的商品，同时为我推荐类似的商品。此外，修改我的收件人号码为17628879789。")],
        "user_id": "log_test", "session_id": "log_test_8",
        "turn_count": 0, "max_turns": 10, "needs_escalation": False,
        "active_agent": None, "conversation_summary": "", "user_profile": None,
        "history_summary": "", "response_meta": None, "react_step_count": 0,
        "max_react_steps": 5, "sub_intents": [], "current_sub_idx": 0, "sub_results": [],
    }
    config = {"configurable": {"thread_id": "log_test_8"}}

    start = time.time()
    counts = {"messages": 0, "updates": 0}
    update_summary = []

    async for event in graph.astream(input_data, config=config, stream_mode=["messages", "updates"], version="v2"):
        elapsed = time.time() - start
        if not isinstance(event, dict):
            continue
        etype = event.get("type", "")
        data = event.get("data")

        if etype == "messages":
            counts["messages"] += 1
        elif etype == "updates":
            counts["updates"] += 1
            if isinstance(data, dict):
                for node_name, output in data.items():
                    update_summary.append(f"[{elapsed:.1f}s] {node_name}")
                    if node_name in ("task_orchestrator", "task_orchestrator_node"):
                        evts = (output.get("orchestrator_events") or []) if isinstance(output, dict) else []
                        if not evts:
                            evt = output.get("orchestrator_event") if isinstance(output, dict) else None
                            if evt: evts = [evt]
                        evt_types = [e.get("type") for e in evts if isinstance(e, dict)]
                        has_events_list = "orchestrator_events" in output if isinstance(output, dict) else False
                        update_summary.append(f"  events: {evt_types} (has_events_list={has_events_list})")
                    elif node_name == "__interrupt__":
                        update_summary.append("  ** INTERRUPT **")

    elapsed = time.time() - start
    print(f"\n=== RESULT ===", flush=True)
    print(f"Time: {elapsed:.1f}s, messages: {counts['messages']}, updates: {counts['updates']}", flush=True)
    print(f"Update summary:", flush=True)
    for line in update_summary:
        print(f"  {line}", flush=True)

    state = await graph.aget_state(config)
    print(f"Final: next={state.next}", flush=True)
    if state.next and any(n in HIGH_RISK_TOOL_NODES for n in state.next):
        print("** HITL INTERRUPT **", flush=True)

if __name__ == "__main__":
    log_file = open(r"d:\Agent\智能客服\test_output.log", "w", encoding="utf-8")
    orig_stdout = sys.stdout
    orig_stderr = sys.stderr
    sys.stdout = log_file
    sys.stderr = log_file
    try:
        asyncio.run(main())
    finally:
        sys.stdout = orig_stdout
        sys.stderr = orig_stderr
        log_file.close()
    with open(r"d:\Agent\智能客服\test_output.log", "r", encoding="utf-8") as f:
        content = f.read()
    # Only print the RESULT section and key orchestrator logs
    for line in content.split("\n"):
        if any(k in line for k in ["RESULT", "Time:", "Update", "  [", "Final:", "HITL", "state check", "跳过plan", "生成plan", "events列表"]):
            print(line)
