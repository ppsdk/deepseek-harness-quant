# DeepSeek HARNESS 深度嵌入说明

本项目将 **DeepSeek HARNESS（DSH）** 深度嵌入：控制页的「控制/对话」区、牛散主观选股桥接、AI 协作能力开箱即用（MIT 许可，随包分发）。

## 一、架构

```
QuantDeck（本项目）
├── deck/deck_server.py       量化 Web 系统（:8787）
└── harness/                  DeepSeek HARNESS 运行时（:3080）
    ├── node_modules/          DSH 及全部依赖（npm 安装，随包自带）
    ├── home/                  DSH_HOME（用户数据根）
    │   ├── .credentials.yaml  ★ API Key 接入点（见下）
    │   ├── settings.yaml.example  旧版模型配置迁移参考（正式路由见 cordis.patch.yml）
    │   ├── profiles/web/      宿主组合（cordis.yml + cordis.patch.yml）
    │   │   └── plugins/dsq-quant-bridge.js   ★ 量化桥接插件（/quant/* 路由）
    │   └── skills/            ★ 预置空白 skill（牛散 7 位模板）
```

- **桥接插件**由 `profiles/web/cordis.patch.yml` 挂载，提供 `/quant/sessions`、`/quant/chat2`、`/quant/niu/*` 等路由——量化控制页原生对话、牛散主观选股自动入远期池全部走它。
- 量化系统与 HARNESS 独立进程、独立端口，通过 HTTP 桥接；任何一方缺失另一方照常运行。

## 二、第一步：接入 AI API Key（关键）

> **核心思路：先接 AI 的 API，AI 就能帮你接入剩下的 API（Tushare 等）。本项目默认接入 DataEyes 中转站，也兼容 DeepSeek 官方。**

1. 在当前提供商创建 API Key：默认 DataEyes（https://platform.dataeyes.ai）；切换官方时使用 DeepSeek（https://platform.deepseek.com/api_keys）
2. 复制模板为正式凭据文件：
   ```bash
   # Windows
   copy harness\home\.credentials.yaml.example harness\home\.credentials.yaml
   # Linux / macOS
   cp harness/home/.credentials.yaml.example harness/home/.credentials.yaml
   ```
3. 用编辑器打开 `harness/home/.credentials.yaml`，默认把 `<your-dataeyes-api-key>` 替换为真实 Key；也可直接运行 `接入API.cmd` 隐藏输入
4. 启动（launcher 或 `python deck/deck_server.py` + `node harness/node_modules/@deepseek-ai/dsh/lib/bin.js web`）
5. 打开 http://127.0.0.1:3080（HARNESS GUI）或量化控制页（:8787/control）——即可与 AI 对话

## 三、让 AI 帮你接入其余 API

接入 AI API Key 后，直接在对话中要求，例如：

> 「请帮我完成 Tushare 数据源接入：把 config/params.yaml 中 data.tushare_token 的配置流程走一遍，并告诉我需要在哪里注册、token 填哪里。」

AI 会指导/协助你完成：
- **Tushare token**（行情/财报数据，https://tushare.pro 注册）
- 其他可选数据源（akshare 免费接口无需 token）
- 系统参数调优、skill 填充等

## 四、预置空白 Skill（打开即自带）

`harness/home/skills/` 已原生注入 7 个空白 skill 模板：

| Skill | 主题 |
|---|---|
| `niu-san-linyuan` | 林园（价值投资派） |
| `niu-san-fengliu` | 冯柳（逆向/弱者体系） |
| `niu-san-chaoguyangjia` | 炒股养家（情绪周期） |
| `niu-san-chenxiaoqun` | 陈小群（游资席位/排雷） |
| `niu-san-zhangmengzhu` | 章盟主（大资金） |
| `niu-san-zhaolaoge` | 赵老哥（打板/妖股） |
| `niu-san-distillation` | 牛散蒸馏方法论（总览框架） |

每个模板含骨架（人物画像/理念/风险批判/因子假设/参考）。**三种填充方式**：
1. 让 AI 填充：对话中要求「请基于公开资料完善 niu-san-linyuan skill」
2. 参考 `niu-san-distillation` 框架自行编辑
3. 导入自己的研究资料（标注来源与可信度）

> 牛散技能填充后，控制页「主观·牛散」分类即出现对应可对话的选股人格（其决策自动入远期池验证）。

## 五、故障排查

| 现象 | 处理 |
|---|---|
| 控制页显示 HARNESS 未连接 | Node.js 未安装 / harness 运行时缺失（先跑 `harness/install.cmd`）/ 3080 端口被占 |
| 对话报鉴权错误 | `.credentials.yaml` 未配置或 Key 无效（检查格式：`version: 1`，默认 Key 位于 `refs.DATAEYES_API_KEY`；切换官方则位于 `refs.DEEPSEEK_API_KEY`） |
| 模型报错/限流 | 当前提供商账户余额不足、模型名或路由不匹配；编辑 `profiles/web/cordis.patch.yml` 的 `agent-default-model.config` 和 `llm-pi-ai` 路由 |
| 牛散对话无反应 | 需 Node + API Key；桥接插件日志见 HARNESS 控制台输出 |

## 六、重新安装 harness 运行时（可选）

```bash
cd harness
npm ci --no-audit --no-fund
```

## 七、DSH 0.2.1-rc.2 兼容说明

当前默认路由为 DataEyes：`https://platform.dataeyes.ai/v1` + `deepseek-v4.1-flash-1017`。若改用 DeepSeek 官方或其他 OpenAI 兼容中转站，需要同步修改 `agent-default-model.config.provider`、对应 `apiKeyEnv`、`baseURL` 和模型名。

- 本项目将 `@deepseek-ai/dsh` 固定为 `0.2.1-rc.2`，锁文件随仓库维护；不会自动跟随 latest/next。
- 本项目安装脚本要求 Node.js 24+，已在 Node.js 24.14.0 下验证。
- 正式模型路由位于 `harness/home/profiles/web/cordis.patch.yml` 的 `agent-default-model.config`。首次运行时，DSH 会把已有 `settings.yaml` 迁移为补丁并改名为 `.imported`；保留迁移副本，不需要重复复制旧文件。
- 凭据使用版本 1 格式：

  ```yaml
  version: 1
  refs:
    DATAEYES_API_KEY: 你的dataeyes-key
  ```

  使用 `接入API.cmd` 或 `setup_api.cmd` 更新 Key；脚本使用 DSH 的文件锁和原子写入，保留 `records` 中的浏览器授权。也可在 HARNESS 模型设置页配置。不要用整文件覆盖方式重置已有凭据。
- 新版 Web GUI 需要启动 URL 中的临时 token 换取浏览器 cookie。`launcher.py` 启动 DSH 时由 DSH 自动打开带 token 的页面；独立启动可运行 `node harness/node_modules/@deepseek-ai/dsh/lib/bin.js web`（先设置 `DSH_HOME`）。仅访问不带 token 的地址可能返回 401。
- 控制页统一经 `/api/proxy/quant/*` 与 `/api/proxy/quant/niu/*` 访问桥接插件；人格元数据来自 `config/niu_personas.json`，缺失时读取同名 `.example`，两者都缺失则返回空目录。
- 可运行 `python validation/dsh_compat_smoke.py`，使用隔离的 HARNESS home 和本地 Messages 模拟服务验证启动、主会话、人格会话、代理 API 与持久化恢复。它不调用真实模型，不能证明真实 API Key、余额或上游网络可用。
