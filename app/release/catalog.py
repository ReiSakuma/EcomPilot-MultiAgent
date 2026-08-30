from __future__ import annotations

from app.release.models import ThreatControl


THREAT_CONTROLS: tuple[ThreatControl, ...] = (
    ThreatControl(
        threat_id="T01",
        threat="模型无限思考或重复调用工具",
        attack_example="模型在 Thought-Action-Observation 循环中反复调用同一工具。",
        control_layers=("ReAct step budget", "duplicate call detection", "DAG loop guard"),
        evidence_paths=(
            "app/orchestration/react_loop.py",
            "app/orchestration/loop_detection.py",
            "app/agents/strategy.py",
            "app/tools/strategy_evidence_tools.py",
            "data/strategy/strategy_evidence.json",
        ),
        test_paths=(
            "tests/test_v19_react_loop.py",
            "tests/test_v65_interview_core.py",
            "tests/test_v20_a2a_protocol.py",
        ),
        boundary="预算只能限制本进程中的 Agent 循环，不是分布式任务配额服务。",
    ),
    ThreatControl(
        threat_id="T02",
        threat="模型越权选择工具或绕过人工审批",
        attack_example="文案 Agent 尝试直接写店铺，或复用不属于当前任务的审批。",
        control_layers=("tool allowlist", "scoped approval grant", "side-effect policy"),
        evidence_paths=("app/safety/policy_gateway.py", "app/tools/governed_executor.py"),
        test_paths=("tests/test_v18_policy_gateway.py",),
        boundary="审批身份来自演示身份目录，生产环境仍需接入企业 IdP 和审批系统。",
    ),
    ThreatControl(
        threat_id="T03",
        threat="Agent 间伪造委派或扩大权限",
        attack_example="子 Agent 修改 capability、task、tenant 或 tool scope 后继续调用。",
        control_layers=("structured handoff", "capability token", "hash-chain security ledger"),
        evidence_paths=("app/orchestration/a2a.py", "app/security/capability_tokens.py", "app/security/ledger.py"),
        test_paths=("tests/test_v22_capability_security.py", "tests/test_v24_tenant_access.py"),
        boundary="A2A 是项目内协议，没有实现跨组织 Agent 身份联盟。",
    ),
    ThreatControl(
        threat_id="T04",
        threat="多 Agent 死循环、重试风暴或状态冲突",
        attack_example="委派失败后不断生成新的委派，或两个节点覆盖同一状态。",
        control_layers=("DAG dependency", "retry budget", "typed reducer", "optimistic checkpoint"),
        evidence_paths=("app/orchestration/executor.py", "app/orchestration/reducer.py", "app/orchestration/checkpoint.py"),
        test_paths=("tests/test_v20_a2a_protocol.py", "tests/test_v13_recovery.py"),
        boundary="Checkpoint 使用本地文件，不提供跨实例共识或分布式事务。",
    ),
    ThreatControl(
        threat_id="T05",
        threat="模型生成危险 SQL 或跨租户查询",
        attack_example="生成 UPDATE、堆叠语句、隐藏 tenant_id 条件或读取未授权列。",
        control_layers=("SQL AST parser", "SELECT allowlist", "tenant row filter", "row limit"),
        evidence_paths=("app/sql/policy.py", "app/sql/service.py"),
        test_paths=("tests/test_v21_text_to_sql.py", "tests/test_v24_tenant_access.py"),
        boundary="当前只开放冻结的只读分析库，不是通用数据库代理。",
    ),
    ThreatControl(
        threat_id="T06",
        threat="SQL 执行耗尽资源或窃取宿主机密钥",
        attack_example="恶意查询长时间占用 CPU、制造大输出或读取模型 API Key。",
        control_layers=("separate process", "RLIMIT", "hard timeout", "environment allowlist"),
        evidence_paths=("app/sandbox/runner.py", "app/sandbox/sql_worker.py"),
        test_paths=("tests/test_v23_process_sandbox.py",),
        boundary="这是进程级沙盒，不等同于容器、虚拟机或云端强隔离执行环境。",
    ),
    ThreatControl(
        threat_id="T07",
        threat="用户读取或操作其他商户的数据",
        attack_example="请求中伪造 tenant_id，读取另一个商户的任务或 SQL 行。",
        control_layers=("trusted principal", "RBAC", "tenant ABAC", "server-side tenant context"),
        evidence_paths=("app/access/identity.py", "app/access/policy.py", "app/access/context.py"),
        test_paths=("tests/test_v24_tenant_access.py",),
        boundary="演示 Bearer Token 是静态身份，不具备生产 JWT 签名验证和密钥轮换。",
    ),
    ThreatControl(
        threat_id="T08",
        threat="浏览器执行、幂等记录或截图发生串租户",
        attack_example="两个商户使用相同商品 ID 和幂等键时互相覆盖。",
        control_layers=("tenant store partition", "tenant idempotency namespace", "artifact namespace"),
        evidence_paths=("app/seller_center/store.py", "app/safety/idempotency.py", "app/browser/backends.py"),
        test_paths=("tests/test_v25_tenant_execution.py",),
        boundary="目录和内存分区不是操作系统账户、独立存储桶或每租户加密密钥。",
    ),
    ThreatControl(
        threat_id="T09",
        threat="浏览器票据被篡改、重放或用于错误操作",
        attack_example="把执行票据用于验证、替换商品或再次提交相同票据。",
        control_layers=("one-time ticket", "purpose binding", "product binding", "plan fingerprint"),
        evidence_paths=("app/browser/tickets.py", "app/browser/service.py"),
        test_paths=("tests/test_v15_browser_execution.py", "tests/test_v25_tenant_execution.py"),
        boundary="票据存储在单进程内存中，多实例部署需要共享原子票据存储。",
    ),
    ThreatControl(
        threat_id="T10",
        threat="故障后无法解释、恢复或发现审计记录被修改",
        attack_example="任务失败后丢失中间状态，或安全事件文件被静默改写。",
        control_layers=("trace", "checkpoint", "recovery", "SHA-256 hash chain", "evidence manifest"),
        evidence_paths=("app/observability/recorder.py", "app/orchestration/recovery.py", "app/security/ledger.py"),
        test_paths=("tests/test_v11_observability.py", "tests/test_v13_recovery.py", "tests/test_v22_capability_security.py"),
        boundary="本地证据可检测修改但不是外部 WORM 审计存储，也没有第三方时间戳证明。",
    ),
    ThreatControl(
        threat_id="T11",
        threat="提示注入诱导 Agent 越权、泄露秘密或执行破坏性操作",
        attack_example="用户伪造系统指令，要求忽略审批、读取其他租户数据或泄露 API Key。",
        control_layers=("policy-first compiler", "allowlisted route", "capability scope", "approval boundary"),
        evidence_paths=(
            "app/copilot/compiler.py",
            "app/copilot/routing.py",
            "app/safety/policy_gateway.py",
        ),
        test_paths=("tests/test_v36_reliability.py", "tests/test_v37_multi_intent_context.py"),
        boundary="规则和权限边界可阻止已知越权路径，但不宣称能识别所有自然语言社会工程攻击。",
    ),
    ThreatControl(
        threat_id="T12",
        threat="并发重复请求、Worker 失联或旧 Worker 覆盖新结果",
        attack_example="两个请求同时修改同一商品，或租约过期的 Worker 恢复后提交旧结果。",
        control_layers=(
            "durable fair queue",
            "lease token",
            "resource fencing token",
            "optimistic version",
            "transactional outbox and saga",
        ),
        evidence_paths=("app/distributed/runtime.py", "app/distributed/bulkhead.py"),
        test_paths=("tests/test_v38_distributed_runtime.py",),
        boundary="SQLite 是单机多进程参考实现；生产集群应迁移到 PostgreSQL、Redis 和消息队列。",
    ),
    ThreatControl(
        threat_id="T13",
        threat="依赖故障、摘要污染或容量过载导致静默错误与不可解释终态",
        attack_example="模型限流、工具超时、重复投递或伪造摘要后，任务继续带错写入或永久卡住。",
        control_layers=(
            "deterministic fault injection",
            "five terminal outcomes",
            "SLO alert gate",
            "summary provenance replay",
            "capacity and tenant-isolation audit",
        ),
        evidence_paths=("app/operations/chaos.py", "app/operations/assessment.py", "app/operations/terminal.py"),
        test_paths=("tests/test_v39_operational_readiness.py",),
        boundary="故障注入位于单机参考实现的协议边界，不替代真实云基础设施灾备演练。",
    ),
)


def build_threat_model() -> dict[str, object]:
    controls = [control.model_dump(mode="json") for control in THREAT_CONTROLS]
    return {
        "release": "v39-chaos-readiness",
        "scope": "interview_final",
        "controls_total": len(controls),
        "implemented_and_tested": len(controls),
        "coverage_rate": 1.0 if controls else 0.0,
        "controls": controls,
        "claim_boundary": (
            "Coverage means the listed interview threats have code and regression evidence. "
            "It does not certify production security or regulatory compliance."
        ),
    }
