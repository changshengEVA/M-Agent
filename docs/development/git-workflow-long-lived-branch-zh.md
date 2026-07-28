# 长期开发分支迭代与合并到 main 的流程

本文说明：当以 **固定分支**（例如 `CSE_update`）长期迭代时，在 **每次迭代开始前** 如何把 `main` 的更新同步进来，以及在 **迭代结束后** 如何把改动 **合并到 `main`**，并回收本地分支状态。

> 适用场景：个人或小团队习惯用一根「功能/集成」分支反复改、反复发 PR，而不是每个小特性都新开 `feat/xxx` 后再删分支。若希望更规范的「一任务一分支」，可参考文末的替代做法。

---

## 1. 心智模型

- **迭代前**：把 `main` 上已被合并的提交同步到当前开发分支，避免在过时基线上继续开发、减少 PR 冲突。
- **迭代中**：在开发分支上正常 `commit` / `push`。
- **迭代后**：通过 GitHub **Pull Request** 把开发分支合入 `main`（推荐 **Squash merge**），再在本地把 `main` 合回开发分支，使两端历史对齐，便于下一轮迭代。

对已长期存在且持续 `push` 到远端的开发分支，本文用 **`merge main`** 同步，而不用 **`rebase`**，以免改写已发布的提交历史。

---

## 2. 迭代开始前：同步 main → 开发分支

工作区最好先 **干净**（无未提交改动）。若有中途半成品，可先：

```bash
git stash push -u -m "wip: brief note"
```

同步步骤（将 `CSE_update` 换成你的开发分支名）：

```bash
git checkout main
git pull --recurse-submodules

git checkout CSE_update
git pull

git merge main
```

若出现冲突：

```bash
git status
# 编辑冲突文件，删除 <<<<<<< ======= >>>>>>> 标记并按需保留内容
git add <resolved-files>
git commit
git push
```

把子模块也拉到与远程一致（若项目使用 submodule）：

```bash
git submodule update --init --recursive
```

恢复之前暂存的改动（若用过 stash）：

```bash
git stash pop
```

---

## 3. 迭代过程中：提交与推送

```bash
git add <path/to/changed-files>
git commit -m "type(scope): short description"
git push
```

建议信息格式采用 [Conventional Commits](https://www.conventionalcommits.org/)（如 `feat:`、`fix:`、`chore:`、`docs:`）。避免习惯性 `git add .`，尤其注意 **子模块**（例如 `data/locomo`）：无意修改时不要提交子模块指针变更。

迭代进行到一半、若 `main` 又合入了较多 PR，可随时 **重复第 2 节**，再 `merge main` 一次。

---

## 4. 迭代结束后：开发分支 → main

### 4.1 推送并开 PR

```bash
git status
git push
```

在 GitHub 上创建 Pull Request：**base** 为 `main`，**compare** 为 `CSE_update`（或你的开发分支）。

命令行示例（需安装 GitHub CLI `gh` 并已登录）：

```bash
gh pr create --base main --head CSE_update --title "..." --body "..."
```

### 4.2 在 GitHub 上合并

- 建议仓库设置中只保留 **Squash and merge**（Settings → General → Pull Requests），使 `main` 上每个 PR 对应一条线性历史，便于回溯。
- 合并后 **不必删除** 长期开发分支；删掉的是本次 PR 的临时分支时才适用。

### 4.3 本地回收：让开发分支再次对齐 main

Squash 合并后，`main` 上是一条新提交，而本地开发分支仍保留原先的多个小提交。为避免下次 PR 显示大量「已包含在 main 中」的差异，应在本地执行：

```bash
git checkout main
git pull --recurse-submodules

git checkout CSE_update
git merge main
git push
```

此后开发分支与 `main` 再次对齐，可进入下一轮：从 **第 2 节** 开始或直接进入 **第 3 节** 开发。

---

## 5. 固定循环（速查）

| 阶段 | 操作 |
|------|------|
| 迭代前 | `main` → `pull` → 切回开发分支 → `pull` → `merge main` → `push` |
| 迭代中 | `add` → `commit` → `push`（可反复；中间可随时再执行迭代前同步） |
| 迭代后 | `push` → GitHub 开 PR → **Squash merge** |
| 回收 | 切 `main` → `pull` → 切开发分支 → `merge main` → `push` |

---

## 6. 注意事项

1. **不要向 `main` 执行 `git push --force`**。长期开发分支若自行 rebase 过已推送历史，才可能需要 `--force-with-lease`；本流程全程用 merge，一般不必强推。
2. **子模块**：`git status` 若出现 `modified: <submodule> (untracked content)`，先进入子模块目录确认是否有意修改；无意修改时不要 `git add` 子模块。
3. **敏感文件**：`.env`、`.secrets/` 等应在 `.gitignore` 中，提交前用 `git status` 确认未误加。
4. **放弃一次未完成的 merge**：`git merge --abort`。

---

## 7. 替代做法：每个任务新开短期分支（推荐在协作变多时采用）

若希望历史更清晰、审查粒度更小：

```bash
git checkout main
git pull --recurse-submodules
git checkout -b feat/short-task-name
# ... 开发与提交 ...
git push -u origin feat/short-task-name
# 发 PR → Squash merge 到 main → 远端删除分支
git checkout main && git pull
git branch -d feat/short-task-name
```

长期分支模式与短期分支模式可以并存：大集成用长期分支，小修小补用 `feat/*`。

---

## 相关文档

- 服务器部署与 Gmail 等机密路径说明：`docs/operations/deployment-server-zh.md`
