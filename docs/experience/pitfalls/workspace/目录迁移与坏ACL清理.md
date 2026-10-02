# 运行时目录迁移：坏 ACL 清理、mv 嵌套与 cache_dir 警告

> 一句话结论：Windows 下 rm/attrib/icacls 全部 Permission denied 的坏 ACL 目录用 `mv` 改名移出仓库即可隔离（rename 只需父目录权限）；目录迁移先 mv 再建目标目录防嵌套；pytest 禁用 cacheprovider 时 cache_dir 属未注册配置项，官方 runner 需忽略 PytestConfigWarning。

## 一、坏 ACL 目录：改名移出 + 管理员删除（2026-10-02 运行时目录收敛实测）

- **表象**：`rm -rf var/tmp` 报 Permission denied；`attrib /s /d` 报"找不到文件"（连枚举子项都失败）；`icacls`/`takeown` 报"拒绝访问"（连读 ACL 都失败）；`cmd rmdir /s /q` 同样失败。17 个目录（var/pytest、var/pytest-tmp、var/test-file-index 等）全部中招。
- **根因**：目录安全描述符损坏（DACL 拒绝一切访问），非提权进程连读取元数据都不行；管理员提权（UAC）无法在非交互 shell 完成。
- **正确姿势**：`mv <坏目录> /d/_liveprofit_acl_trash/<名>`——rename 只需要**父目录**写权限，与子目录自身 ACL 无关，同卷移动零复制。把全部坏目录移出仓库后，仓库内立即达成目录纯度目标；最终物理删除留给用户以管理员权限完成（资源管理器删除会弹 UAC）。注意事后要告知用户隔离目录位置。

## 二、mv 与 mkdir -p 的嵌套陷阱

- **表象**：`mkdir -p var/data/backups` 预创建目标后执行 `mv var/logs/backups var/data/backups`，结果是 `var/data/backups/backups/`——数据藏在嵌套子目录里，验收清单看着"在位"实则路径错了一层。
- **根因**：mv 到已存在目录 = 移入该目录内部（与 cp 语义一致）。
- **正确姿势**：迁移命令先 mv 再补缺目录（或 mv 后显式检查嵌套并修正）；目标路径必须在迁移后逐项 `ls` 复核落点，不能只看 mv 命令成功。

## 三、pytest cache_dir 与 no:cacheprovider 的配置警告

- **表象**：pyproject 配 `cache_dir = "var/cache/pytest"` 后，官方入口 tests/run.py（带 `-p no:cacheprovider`）每次运行输出 `PytestConfigWarning: Unknown config option: cache_dir`。
- **根因**：cacheprovider 插件被禁用时 cache_dir ini 项未注册，属"配置存在但插件缺席"的预期警告；若将来启用 `--strict-config` 会升级为错误。
- **正确姿势**：runner 命令加 `-W ignore::pytest.PytestConfigWarning`（tests/run.py:82-83）；裸跑 pytest 不受影响，缓存正常落 var/cache/pytest。
