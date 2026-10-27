# 第三方组件许可声明

本文件列出本项目（校园助手 / campus-assistant）使用的第三方组件及其许可证。
本项目自身代码以 **MIT** 许可发布，见根目录 `LICENSE`。

各组件的完整许可证文本随包安装于 Python 虚拟环境的 `Lib/site-packages/<包名>-<版本>.dist-info/` 目录下。
本文件仅作汇总，不替代各组件原始许可证。

## 一、组件清单

| 组件 | 版本 | 许可证 | 在本项目中的用途 |
| --- | --- | --- | --- |
| FastAPI | 0.141.1 | MIT | Web 服务框架 |
| Uvicorn | 0.54.0 | BSD-3-Clause | ASGI 服务器 |
| Ollama | 0.40.6 | MIT | 本地模型运行时，监听 127.0.0.1:11434 |
| python-multipart | 0.0.32 | Apache-2.0 | 表单与文件上传解析 |
| python-docx | 1.2.0 | MIT | Word 文档解析 |
| pypdf | 6.19.0 | BSD-3-Clause | PDF 文档解析 |
| jieba | 0.42.1 | MIT | 中文分词 |
| rank-bm25 | 0.2.2 | Apache-2.0 | BM25 关键词检索 |
| sentence-transformers | 6.1.0 | Apache-2.0 | 嵌入与重排模型加载 |
| PyTorch (CPU) | 2.14.0 | BSD-3-Clause | 向量编码与重排推理后端 |
| NumPy | 2.5.3 | BSD-3-Clause | 向量计算 |

## 二、模型权重

| 模型 | 许可证 | 说明 |
| --- | --- | --- |
| Qwen3-8B | Apache-2.0 | 由 Ollama 从官方模型库拉取，不随本仓库分发 |
| BAAI/bge-small-zh-v1.5 | MIT | 由安装脚本从 HuggingFace 下载，不随本仓库分发 |
| BAAI/bge-reranker-base | MIT | 同上 |

模型权重不纳入本仓库发布包，安装脚本仅引导使用者从官方渠道或公开镜像下载，
使用者自行下载即自行承担相应许可义务。

## 三、各组件的关键义务

### MIT（FastAPI、python-docx、jieba、bge 系列）

保留版权声明与本许可声明；可自由使用、修改、再分发；作者不提供担保。

> jieba 的发行包内未附 LICENSE 文件，其 MIT 许可记录在 PyPI 元数据
> （`License :: OSI Approved :: MIT License`）中。此处按其元数据标注列出。

### BSD-3-Clause（Uvicorn、pypdf、PyTorch、NumPy）

保留版权声明、许可条款与免责声明；未经书面许可，不得使用版权方或贡献者名义为衍生产品背书。

### Apache-2.0（python-multipart、rank-bm25、sentence-transformers、Qwen3-8B）

保留版权、专利、商标与归属声明；修改过的文件须显著标注修改；
随分发保留 `LICENSE` 与 `NOTICE` 文件（`sentence-transformers` 即附有 `NOTICE.txt`）。

## 四、本项目已落实的事项

1. 未删改任何组件的版权与许可声明；
2. 未对上述组件作与原作者名义相关的宣传或背书性表述；
3. 未使用来源不明、或仅「公开可访问」而无明确再利用授权的资源；
4. 模型权重不纳入发布包，避免承担再分发义务；
5. 校本资料不随代码公开，其著作权归提供资料的学校。

---

最后核对时间：2026-10-22
核查方式：读取各组件安装包的 `LICENSE` / `NOTICE` 文件与 PyPI 元数据字段，逐项比对。
