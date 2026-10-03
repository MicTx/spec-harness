# 《从零构建 Agent Harness 智能体应用开发实践》

这是一本中文技术电子书，讲清 Agent Harness 的工程工作流控制层：为什么 Agent 会失控，任务包怎样保存上下文，门禁怎样把“看起来完成”变成可验证结论。它不是完整模型运行时，也不保证商业结果。

## 阅读与下载

- [EPUB 电子书](dist/from-zero-agent-harness.epub)
- [完整 Markdown 书稿](src/book.md)
- [目录](src/outline.md) · [术语](src/glossary.md)
- [教学程序说明](examples/README.md)

陈默、拾光相册以及其用户数量、事故和时间线为教学虚构；本书构建与测试结论以本仓实际记录为依据。

## 构建与测试

需要 **Python 3.9+** 和 **Pandoc 3.x**（本次使用 3.8.3）。macOS 可用 `brew install pandoc`；其他平台按 Pandoc 官方安装说明。没有第三方 Python 包依赖。

从仓库根目录执行：

```bash
python3 book/verify_ebook.py --skeleton
python3 book/verify_ebook.py --chapters 00,01,02
python3 book/verify_ebook.py
python3 -m unittest discover -s book/examples -v
python3 -m unittest discover -s book/tests -v
```

全量模式会重建 `src/book.md`，构建临时 EPUB，检查 ZIP/OPF/spine/XHTML/章节标题及内部链接，通过后才替换正式 EPUB。构建失败时退出非零，既有 EPUB 不代表本次成功。集成测试读取构建产物，请先运行全量构建。

字数规则统计 CJK 字符，不是含英语代码的字符总数。结构、字数和构建通过不等于内容质量或商业成绩；技术与编辑复核另有记录。

## 教学版本与参考实现

`examples/mini_harness.py` 采用 `.mini-harness` 和单写者固定格式，支持 init/route/check/verify；不自动调用模型、调度助手、操作 Git 或发布远端。参考实现使用 `.spec` 和更完整的协作机制，二者不宣称格式兼容。

源稿编辑 `src/` 下的章节文件，不直接修改组装稿或 EPUB。发布前运行本页列出的验证命令，构建产物通过检查后再分发。

本轮不含纸书、ISBN、平台上架、真实销量验证或生产数据迁移。
