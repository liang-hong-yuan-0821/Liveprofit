# Git Bash 运行脚本注意（Windows）

> 一句话结论：Git Bash 下打开浏览器/外部程序用 `powershell.exe -NoProfile -Command "Start-Process '...'"` + `</dev/null` 兜底，禁用 `cmd.exe /c "start ..."`。

## cmd.exe /c "start ..." 会被 MSYS 路径转换破坏

- **表象**：终端出现 cmd 横幅后卡住（进入**交互式会话**），后续步骤不再执行，脚本挂起。
- **根因**：Git Bash 下 `/c` 会被 MSYS 路径转换破坏。
- **正确姿势**：改用 `powershell.exe -NoProfile -Command "Start-Process '...'"`，并加 `</dev/null` 兜底防交互式挂起（见 run.sh）。
