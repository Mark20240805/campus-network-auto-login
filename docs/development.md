> [← 返回 README](../README.md) · 相关：[构建分发](build.md) · [排错](troubleshooting.md)

# 开发与发布：测试 · 版本同步 · GitHub

## 开发与测试

核心逻辑（脱敏、失败分类、退避计算、Cookie 分域、配置读写）都有单元测试，
不需要真实校园网环境即可跑：

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q
```

```
158 passed
```

目前共 158 项，分布在 `test_core.py`、`test_config_log.py`、`test_portal_parse_edge.py`、
`test_portal_parse_ui.py`、`test_dns_fallback.py`、`test_reliability.py` 六个文件；
各文件覆盖范围见 [tests/README.md](../tests/README.md)。

- `tests/conftest.py` 注入了 tkinter 的测试替身，所以 **无图形环境也能跑**（GitHub Actions 的 Windows runner 用的就是这套）
- 测试数据全部是虚构的学号 / MAC / IP，不含任何真实个人信息
- 改动认证流程后请先跑一遍，再重新打包

## 版本与发布

当前版本：**1.1.0**，变更见 [CHANGELOG.md](../CHANGELOG.md)。

发版时要一起改这四处，否则界面标题、exe 属性和安装包版本会对不上：

| 位置 | 字段 |
|---|---|
| `校园网自动登录.py` | `APP_VERSION` |
| `installer\campusnet.iss` | `MyAppVersion` |
| `tools\version_info.txt` | `filevers` / `prodvers` / `FileVersion` / `ProductVersion` |
| `tools\sign.ps1` | `$Files` 里的安装包文件名 |

## 上传到 GitHub

`.gitignore` 排除了 `config.json`、`login.log`、`stage_nuitka\`、`installer\output\` 等
个人数据和打包产物。推之前先自检：

```bash
git ls-files                    # 只应看到源码、文档、脚本、图标、证书
git log --format="%ae" | sort -u # 只应看到 noreply 邮箱，不应出现私人邮箱
git status --short               # 应为空
```

第一次推送：

```bash
git remote add origin https://github.com/<你的用户名>/<仓库名>.git
git push -u origin main
git push origin v1.1.0          # 推标签（Release 要用）
```

exe 和安装包**不进仓库**，作为 [Release](https://docs.github.com/repositories/releasing-projects-on-github)
附件上传：在 GitHub 页面 `Releases → Draft a new release`，选 `v1.1.0` 标签，附件只放两个文件：

- `installer\output\校园网自动认证系统_安装包_1.1.0.exe`
- `installer\output\SHA256SUMS.txt`

**不要上传 `stage_nuitka` 里的裸 exe**：standalone 主程序单独下载缺依赖跑不起来，
历来只发布安装包。正文直接复用 `CHANGELOG.md` 的 1.1.0 段落。

生成校验清单时注意：GitHub 会把中文附件名转写成英文名，清单里必须写转写后的名字，
否则用户下载后 `sha256sum -c` 会因找不到文件而失败：

```bash
certutil -hashfile "installer\output\校园网自动认证系统_安装包_1.1.0.exe" SHA256
# 把输出的 64 位哈希写进 installer\output\SHA256SUMS.txt，格式为：
# <哈希>  campus-network-auto-login_Setup_1.1.0.exe
```

万一以后误提交了 `config.json`，执行 `git rm --cached config.json` 再提交，
并把它从历史里清掉（`git filter-repo`）——里面是你的学号和本机加密的密码。
