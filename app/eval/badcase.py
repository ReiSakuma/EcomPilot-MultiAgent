BAD_CASE_TYPES = [
    "Planning Error",
    "Routing Error",
    "Context Loss",
    "Memory Error",
    "Retrieval Error",
    "Tool Selection Error",
    "Tool Parameter Error",
    "Tool Execution Error",
    "Model Generation Error",
    "Environment Error",
    "Cross-Agent Conflict",
    "Constraint Violation",
    "Price Confirmation",
    "Browser Execution Error",
]


def classify_bad_case(state) -> dict[str, object]:
    review = state.agent_outputs.get("review_agent", {})
    browser = state.agent_outputs.get("browser_agent", {})
    failed_nodes = [
        node_id for node_id, node in state.nodes.items() if getattr(node, "status", None) == "failed"
    ]
    failed_tools = [record for record in state.tool_records if record.get("status") == "failed"]
    if not state.constraints.get("category"):
        case_type = "Planning Error"
        root_cause = "required_category_missing"
        owner = "planner"
        severity = "high"
        recoverable = True
        expected_guardrail = False
        recommended_action = "Ask for or infer a supported product category before planning."
    elif (
        state.status == "waiting_for_input"
        and state.agent_outputs.get("market_price_gate_agent", {}).get("status")
        == "confirmation_required"
    ):
        case_type = "Price Confirmation"
        root_cause = str(
            state.agent_outputs["market_price_gate_agent"].get("reason_code")
            or "market_price_confirmation_required"
        )
        owner = "market_price_gate_agent"
        severity = "info"
        recoverable = True
        expected_guardrail = True
        recommended_action = (
            "Adopt the suggested range, keep the original price with evidence, "
            "or finish as market analysis only."
        )
    elif review.get("violations"):
        case_type = "Constraint Violation"
        root_cause = "business_constraint_rejected"
        owner = "review_agent"
        severity = "medium"
        recoverable = True
        expected_guardrail = True
        recommended_action = "Revise the listing or strategy, then rerun review."
    elif state.status == "waiting_for_approval":
        case_type = "Browser Execution Error"
        root_cause = "human_approval_missing"
        owner = "approval_guardrail"
        severity = "info"
        recoverable = True
        expected_guardrail = True
        recommended_action = "Resume from the approval gate after explicit human approval."
    elif failed_tools:
        latest_tool = failed_tools[-1]
        error_type = latest_tool.get("error_type")
        if error_type == "ToolPermissionError":
            case_type = "Tool Selection Error"
            root_cause = "tool_permission_denied"
        elif error_type == "ToolParameterError":
            case_type = "Tool Parameter Error"
            root_cause = "tool_input_contract_rejected"
        elif error_type == "ToolResultValidationError":
            case_type = "Tool Execution Error"
            root_cause = "tool_result_contract_rejected"
        elif error_type == "ToolTimeoutError":
            case_type = "Tool Execution Error"
            root_cause = "tool_timeout_after_retries"
        else:
            case_type = "Tool Execution Error"
            root_cause = "tool_execution_failed"
        owner = str(latest_tool.get("tool_name", "tool_registry"))
        severity = "high"
        recoverable = error_type in {"ToolTimeoutError", "TransientToolError"}
        expected_guardrail = error_type in {
            "ToolPermissionError",
            "ToolParameterError",
            "ToolResultValidationError",
            "ToolApprovalRequiredError",
        }
        recommended_action = "Inspect the failed tool contract and retry policy before recovery."
    elif failed_nodes:
        if "market" in failed_nodes:
            case_type = "Retrieval Error"
            root_cause = "market_dataset_not_found"
            owner = "market_agent"
            recommended_action = "Add a category dataset or configure an external data provider."
        elif "browser" in failed_nodes:
            case_type = "Browser Execution Error"
            root_cause = "execution_or_verification_failed"
            owner = "browser_agent"
            recommended_action = "Inspect execution and verification tool events before retrying."
        else:
            case_type = "Tool Execution Error"
            root_cause = "agent_or_tool_execution_failed"
            owner = failed_nodes[0] if failed_nodes else "workflow_executor"
            recommended_action = "Inspect the failed node and its latest tool event."
        severity = "high"
        recoverable = True
        expected_guardrail = False
    elif browser.get("browser_result", {}).get("status") != "applied" and browser:
        case_type = "Browser Execution Error"
        root_cause = "seller_center_state_not_applied"
        owner = "browser_agent"
        severity = "high"
        recoverable = True
        expected_guardrail = False
        recommended_action = "Compare the execution plan with observed seller-center state."
    else:
        case_type = "Planning Error"
        root_cause = "unclassified_failure"
        owner = "supervisor"
        severity = "medium"
        recoverable = False
        expected_guardrail = False
        recommended_action = "Review the full trace and add a more specific classifier rule."
    return {
        "run_id": state.run_id,
        "task_id": state.task_id,
        "status": state.status,
        "case_type": case_type,
        "failed_nodes": failed_nodes,
        "violations": review.get("violations", []),
        "constraints": state.constraints,
        "root_cause": root_cause,
        "owner": owner,
        "severity": severity,
        "recoverable": recoverable,
        "expected_guardrail": expected_guardrail,
        "recommended_action": recommended_action,
        "failed_tools": [
            {
                "tool_name": record.get("tool_name"),
                "error_type": record.get("error_type"),
                "attempt_count": record.get("attempt_count", 1),
            }
            for record in failed_tools
        ],
    }
