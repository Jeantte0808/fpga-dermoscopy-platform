# GitHub发布步骤

## 推荐GitHub Desktop

1. 登录GitHub Desktop，选择File → Add local repository，选择本仓库文件夹。如提示不是Git仓库，选择Create a repository here，保持目录指向本文件夹。
2. 检查Changes，确认只有hardware、software、docs、README和声明文件，无密钥、数据集、模型或构建文件。
3. Summary填写Initial source release，然后Commit to main。
4. 点Publish repository，名称建议fpga-dermoscopy-platform。首次勾选Keep this code private，先上传审核。
5. 团队确认许可后添加LICENSE并提交；准备公开时在GitHub网页Settings → General → Danger Zone → Change visibility切Public。
6. README中补充实际作者/仓库链接；若提供程序包，在确认分发权限后用Releases上传，不把整个工作目录拖入仓库。

## Git命令方式

在本文件夹打开终端，确认安装Git并设置你自己的user.name/user.email，随后：

```powershell
git init -b main
git add .
git status
git commit -m "Initial source release"
git remote add origin https://github.com/YOUR_ACCOUNT/fpga-dermoscopy-platform.git
git push -u origin main
```

YOUR_ACCOUNT替换为你的账号。网页先创建同名空仓库，不额外初始化README、LICENSE或.gitignore，避免首次推送冲突。不要把GitHub token写入URL或代码。具体界面以GitHub当前页面为准。

官方说明：https://docs.github.com/en/repositories/creating-and-managing-repositories/creating-a-new-repository
