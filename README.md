# QC45 Menu

Unofficial macOS menu-bar controls for your own Bose QuietComfort 45.
[中文说明](#中文) is below.

This is not a Bose product and is not affiliated with Bose. It writes the
settings below to the headphones. Those settings stay on the headphones after
the Mac is disconnected. It cannot replace the factory voice-prompt recordings.

## What it controls

- Noise control (CNC), 11 steps from 0 to 10, on Custom 1 or Custom 2.
  0 is the strongest noise cancelling and 10 is the most ambient sound.
- Three-band equalizer, from -10 to +10, saved on the headphones.
- Presets: Hip-Hop +6/+1/-2, Pop +4/+2/-2, Rock +2/+3/-1.
- Modes: Quiet, Aware, Custom 1, Custom 2.
- Wind block in a custom mode. Turning it on stores CNC 0 for that mode.
- Sidetone, voice-prompt on/off, the prompt languages the headphones report, and auto standby.
- Interface language: English or Simplified Chinese. The first launch follows
  the Mac language. The choice is saved on this Mac in `~/.qc45/ui-language`.

Changing the interface language does not change the headphone prompt language.

## Run it on your Mac

You need macOS, a paired QC45, and the system Python 3 from `/usr/bin/python3`.
Bluetooth permission is tied to that Python, so start the app from Terminal.
Opening a bundled `.app` from Finder is not the supported path.

1. Pair and connect the QC45 in System Settings.
2. Download or clone this repository.
3. Double-click `Launch QC45 Menu.command`.
4. If macOS asks, allow Terminal to use Bluetooth.
5. The headphones icon appears in the menu bar. You can minimize the Terminal window. Closing it quits the app.

The first launch installs the three Python Bluetooth packages listed in
`requirements.txt` for this Mac's Python. To install them yourself:

```sh
/usr/bin/python3 -m pip install --user -r requirements.txt
/usr/bin/python3 menubar/qc45menu.py
```

Optional copy into your Applications folder:

```sh
./install.sh
```

Remove that copy with `./install.sh --uninstall`.

The app looks for a paired QC45. If several Bose products are paired, it prefers
the connected QC45. To choose one address, set `QC45_MAC` to that address.
Nothing in this repository contains a personal device address.

```sh
QC45_MAC="AA:BB:CC:DD:EE:FF" /usr/bin/python3 menubar/qc45menu.py
```

A short diagnostic log is written to `~/.qc45/app.log`. Before a setting is
changed, the previous value is saved under `~/.qc45/backups/`.

## Checks without headphones

```sh
/usr/bin/python3 -m unittest discover -s menubar -p 'test_*.py' -v
```

These tests do not connect to a device. Real headphone behavior depends on the
paired QC45 and its firmware.

## Credit

The `pybmap/` library comes from [bosectl](https://github.com/aaronsb/bosectl)
by Aaron Bockelie and stays under its MIT license (`pybmap/LICENSE`). The menu
application is also MIT (`LICENSE`).

## 中文

这是一个非官方的 Mac 菜单栏工具，用来调节你自己的 Bose QuietComfort 45。
它与 Bose 没有关联。下面这些设置会写进耳机，离开电脑后仍然保留。
它不能把你自己的录音替换成耳机里的原厂提示音。

可以调节：

- 降噪 / 环境音 CNC，0 到 10。0 是最强降噪，10 是最大环境音。需要先选自定义 1 或自定义 2。
- 三段均衡，每段 -10 到 +10，保存在耳机里。
- 预设：说唱 +6/+1/-2，流行 +4/+2/-2，摇滚 +2/+3/-1。
- 模式：消噪、通透、自定义 1、自定义 2。
- 自定义模式下的抗风噪。打开后，这个模式的 CNC 会记成 0。
- 通话自声、语音提示开关、耳机报告自己支持的提示语言，以及自动待机。
- 界面语言：简体中文或 English。第一次启动跟随 Mac 语言，之后可在面板里切换。选择保存在这台 Mac 的 `~/.qc45/ui-language`，不会改耳机的提示语言。

在自己的 Mac 上使用：

1. 在系统设置里配对并连接 QC45。
2. 下载或克隆这个仓库。
3. 双击 `Launch QC45 Menu.command`。
4. 如果系统询问，允许终端使用蓝牙。
5. 菜单栏出现耳机图标。终端窗口可以最小化；关掉窗口会退出应用。

请使用系统自带的 `/usr/bin/python3`。蓝牙权限认的是这个程序，不建议从访达直接打开打包的应用。

第一次启动时，会为本机 Python 安装 `requirements.txt` 里的三个蓝牙相关包。也可以自己安装后启动：

```sh
/usr/bin/python3 -m pip install --user -r requirements.txt
/usr/bin/python3 menubar/qc45menu.py
```

可选安装到个人应用目录：

```sh
./install.sh
```

用 `./install.sh --uninstall` 删除这份副本。

程序会自动寻找已配对的 QC45。如果配对了多台 Bose，优先使用已经连接的 QC45。
也可以用 `QC45_MAC` 指定地址。仓库里不包含任何人的耳机地址。

不连接耳机也能检查代码：

```sh
/usr/bin/python3 -m unittest discover -s menubar -p 'test_*.py' -v
```

这些测试不会连接耳机。实际效果取决于你自己的 QC45 和它的固件。

`pybmap/` 来自 Aaron Bockelie 的 [bosectl](https://github.com/aaronsb/bosectl)，继续使用其 MIT 许可（`pybmap/LICENSE`）。菜单栏程序本身也是 MIT（`LICENSE`）。
