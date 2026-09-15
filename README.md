# APIOne 批量调用工具

这是一个**便携式 Python 目录程序**，不生成 exe 或 app。程序主体全部由 Python 编写；官方 `apione-http-client-1.0.3-RELEASE.jar` 仅作为 APIOne 鉴权和 HTTP SDK 使用，Python 不自行实现签名。任务中的 JAR 路径按程序目录保存为相对路径，复制整个目录到其他电脑后仍可读取。

## 目录运行方式

发行目录中应包含：

```text
apione-batch-tool/
├── apione_batch_gui.py
├── ApioneBatchSdkBridge.class
├── apione-http-client-1.0.3-RELEASE.jar
├── 启动_APIOne批量工具.command
├── 启动_APIOne批量工具.bat
├── 启动_APIOne批量工具.sh
├── runtime/
│   ├── macos-arm64/bin/python3
│   ├── macos-x64/bin/python3
│   └── windows-x64/python.exe
├── data/
├── logs/
├── results/
└── update/
```

### macOS

双击：

```text
启动_APIOne批量工具.command
```

也可以在终端运行：

```bash
./启动_APIOne批量工具.sh
```

### Windows

在终端运行：

```bat
启动_APIOne批量工具.bat
```

不要求用户安装 Python。`runtime/` 中需要放对应系统和 CPU 架构的便携式 Python 运行时。macOS ARM、macOS Intel、Windows x64 应分别提供运行时，不能跨平台混用。

> 当前 macOS ARM 运行时已补齐并通过 Python 3.13.12 + Tkinter 导入验证；`runtime/macos-x64` 和 `runtime/windows-x64` 仍需在对应平台填充同架构运行时，不能跨平台混用。

## 首次使用

在界面中填写 AK/SK；也可以通过环境变量传入：

```bash
export APIONE_AK='你的AK'
export APIONE_SK='你的SK'
```

AK/SK 不写入普通日志和结果文件。

## 默认接口配置

```text
请求地址：https://data-elem.digitaljx.com/apione/
Region：INTER
API Name：RS36000000000012026091420374688
path：空字符串
Content-Type：application/json
```

注意：公网地址末尾 `/` 必须保留。

## 操作按钮

- **执行一次（测试）**：只发起 1 次请求，适合先验证接口和凭证。
- **执行批量任务**：按 ID 范围、总次数、时间窗口和频率跑批。
- **停止任务**：当前请求结束后停止后续请求。

## 当前批处理规则

- ID 最小值、最大值：默认 1～100；每次随机生成一个 JSON 整数。
- 调用总次数：控制最多发起的请求数。
- 固定频率：每次调用之间固定等待。
- 区间随机频率：每次调用之间在最小/最大间隔内随机等待。
- 任务时间窗口：格式为 `YYYY-MM-DD HH:MM:SS`。
- 重试次数：单次失败后的额外重试次数。
- 结果输出：JSON Lines 文件。

当前请求体示例：

```json
{"id":76}
```

## 版本和远程更新约定

当前版本记录在：

```text
version.json
```

当前目录版本为 `0.4.0`。当前可直接在本机 macOS ARM 上测试；Intel Mac 和 Windows 发行目录仍需填充对应架构的 Python 运行时。GUI 顶部提供“环境检查”和“检查更新”按钮；更新模块支持本地或 HTTPS manifest、平台包下载、SHA-256 校验、保留用户数据目录和失败回滚。当前远程更新清单为：

```text
https://raw.githubusercontent.com/YQing0123/apione-batch-tool/main/manifest.json
```

正式发布新版本时，更新远程 `manifest.json` 的版本号、发行包地址和 SHA-256，并将对应平台的完整发行压缩包上传到 GitHub Releases。

远程仓库建议保存源代码和发行压缩包，客户端不直接执行 `git pull`，而是读取远程 `manifest.json`，根据当前平台下载对应目录包，校验 SHA-256 后更新。GUI 顶部的“检查更新”按钮会执行这套流程。

示例模板：

```text
manifest.example.json
```

更新流程：

```text
检查版本 → 下载平台包 → SHA-256 校验 → 备份当前目录 → 替换程序文件 → 失败回滚
```

更新时应保留：

```text
data/
logs/
results/
```

## 本地测试与验证

### 更新入口（GUI「检查更新」）

GUI 顶部的「检查更新」按钮会读取上述 GitHub Raw 远程 `manifest.json`，
比对版本、下载当前平台包、校验 SHA-256、备份并替换程序文件。正式发布前，
必须填入真实发行压缩包地址与 SHA-256；本地联调仍可用 `file://` 或本地路径的 manifest：

```bash
# 用真实 JAR/代码打一个平台包，并算出 SHA-256
cd /path/to/apione-batch-tool
zip -r /tmp/APIOneBatchTool-0.3.0-macos-arm64.zip . -x '*.git*' 'update/backups/*'
SHA=$(shasum -a 256 /tmp/APIOneBatchTool-0.3.0-macos-arm64.zip | awk '{print $1}')
# 写一份指向本地包的 manifest（version 高于当前 0.3.0 才会提示更新）
python3 - <<'PY'
import json, pathlib
m = json.loads(pathlib.Path("manifest.example.json").read_text(encoding="utf-8"))
m["version"] = "0.3.1"
import subprocess
sha = subprocess.check_output(["shasum","-a","256","/tmp/APIOneBatchTool-0.3.0-macos-arm64.zip"]).decode().split()[0]
m["packages"]["macos-arm64"]["url"] = "file:///tmp/APIOneBatchTool-0.3.0-macos-arm64.zip"
m["packages"]["macos-arm64"]["sha256"] = sha
pathlib.Path("/tmp/manifest.json").write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
print("wrote /tmp/manifest.json")
PY
# 启动时传入该 manifest 即可走完整「检查→下载→校验→安装→回滚」路径
python3 apione_batch_gui.py   # 在「检查更新」对话框中粘贴 file:///tmp/manifest.json
```

> 不提供 `manifest.json` 时点击「检查更新」会提示“未找到更新清单”，属预期行为，
> 不影响其他功能。

### Java 检测

执行任务前会自动调用 `java_env.detect_java`：未安装 Java、版本低于 17、或
macOS 仅有 `/usr/bin/java` 桩（无 JRE）都会被判为不可用并弹出安装引导；安装后
再次点击执行会重新检测，不会沿用旧的否定缓存。

```bash
python3 -c "import java_env; print(java_env.detect_java())"
```

### SDK 实际调用

真实调用需要本机具备 **JDK 17+** 与可用的 `APIONE_AK/SK`、以及对端服务可达。
这两项为运行环境/凭证依赖，不在此仓库内置；缺任一项时执行会给出明确错误而非静默成功。

## JAR 和 Java 说明

运行工具前会自动检测 Java。由于当前 `ApioneBatchSdkBridge.class` 按 JDK 17 编译，实际运行环境要求 **JDK 17 或更高版本**。缺少 Java 或版本过低时，执行按钮不会发起请求，界面会显示标准化安装命令；只有用户点击安装按钮或打开官方安装页后才会执行对应动作。

macOS：`brew install --cask temurin17`

Windows：`winget install --interactive Microsoft.OpenJDK.17`

Linux（Debian/Ubuntu）：`sudo apt-get update && sudo apt-get install -y openjdk-17-jdk`

也可以手动打开 Adoptium 或 Microsoft OpenJDK 官方下载页。安装后重新点击执行。

SDK 使用 JDK 内部 Base64 类。JDK 17 运行时需要：

```text
--add-exports=java.xml/com.sun.org.apache.xerces.internal.impl.dv.util=ALL-UNNAMED
```

因此最终做到“完全免环境”时，除了便携式 Python，还需要在发行目录中提供兼容的 Java 运行时，或提供已经封装好的 SDK Bridge 运行组件。当前目录仍使用系统 `java` 命令，最终发行包制作时必须补充这一项。
