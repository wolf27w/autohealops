# API 契约（OpenAPI）—— M6

> 整理：小吴　2026-10-15

## 目的
把后端接口契约**固化进仓库**（`docs/openapi.json`），并在 CI 里做**漂移检查**：接口改了却忘记更新契约 → CI 直接失败，杜绝前后端接口悄悄不一致。

## 用法

```bash
./scripts/openapi.sh           # 导出/更新契约 → docs/openapi.json（改完接口后提交）
./scripts/openapi.sh --check   # 重新生成并与已提交契约比对，不一致则非 0（CI 自动跑，见 ci_check.sh 第 7 步）
```

- 契约来源：`app.main:app.openapi()`（FastAPI 自动生成，245 个路径）。
- 导出时对 `operationId` 按「方法+路径」**规范化**，规避 catch-all 路由（`/api/v1/ai/{path:path}` 多方法）的 operationId 冲突导致的非确定性。

## 前端类型（可选）
如需自动生成前端类型（需自行安装工具，非强制）：

```bash
cd frontend && npx openapi-typescript ../docs/openapi.json -o src/generated/api-types.ts
```

现状：前端类型目前手写在 `src/services/api.ts`（`http.get<T>` 泛型门面）。契约 + 漂移检查已能兜住「路径/方法/请求体结构」的变化；类型自动生成属进一步优化，可按需引入。

## 约束
- 改接口 → 必须 `./scripts/openapi.sh` 重新导出并提交 `docs/openapi.json`，否则 CI 第 7 步失败。
- 已知告警：`ai_proxy` 多方法路由 FastAPI 会报 duplicate operationId（不影响运行，已在导出时规范化）。
