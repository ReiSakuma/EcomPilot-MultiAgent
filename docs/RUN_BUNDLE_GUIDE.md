# 单次运行证据包导出

当用户页面完成一次方案生成或店铺同步后，可以将该任务涉及的模型、工具、SQL、沙盒、A2A、审批、权限、Trace 与店铺状态一次性导出。

## 推荐方式

保持 `python scripts/run_linked_service.py` 正在运行，另开终端执行：

```bash
cd /home/theburningmuses/EcomPilot_MultiAgent/v35
python scripts/export_run_bundle.py
```

不填写任务 ID 时导出最新任务。脚本最后会打印 ZIP 的绝对路径。

导出指定任务：

```bash
python scripts/export_run_bundle.py --task-id task_xxxxxxxx
```

输出默认保存在：

```text
reports/run_bundles/ecompilot_<task_id>_<run_id>.zip
```

把这个 ZIP 作为附件发送即可。v35 的 ZIP 内包含：

- `run_bundle.json`：任务、模型、工具、审批、A2A、安全和运行时汇总。
- `conversation.json`：本轮所属会话、消息、回答、记忆摘要和商品追踪。
- `protocol_manifest.json`：跨模块协议及版本。
- `bundle_manifest.json`：其余条目的 SHA-256、大小和路径。
- `raw/`：原始 Checkpoint 与 Trace；`artifacts/`：本次运行的截图。

建议在联动服务运行时导出。服务离线时仍可导出磁盘上的任务、Trace、A2A 和安全账本，但无法取得只存在于当前服务进程中的 SQL 审计、访问审计和 Seller Center 内存状态；汇总文件会明确标记这些部分为 `service_offline`。
