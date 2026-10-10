# ReviewFlow Server Deployment Guide (Ubuntu)

[中文](#zh-cn) · [English](#en) · [Français](#fr) · [Русский](#ru) · [Español](#es) · [日本語](#ja) · [Português](#pt) · [Deutsch](#de) · [Српски](#sr) · [한국어](#ko)

<a id="zh-cn"></a>
## 中文

本指南依据 Ubuntu 24.04 上完成的团队协作模式部署整理。Ubuntu 20.04、22.04、24.04 均可作为部署环境；建议使用 Python 3.12。网络需能访问 GitHub。建议至少 2 GB 内存、10 GB 磁盘，并安装 Nginx。其他 Ubuntu 版本或 Python 版本请先核对依赖兼容性。

命令中的 `/home/ubuntu` 是示例目录，请替换为实际服务器账户和源码位置。

### 1. 安装系统依赖并获取源码

```bash
sudo apt-get update
sudo apt-get install -y python3-venv libcairo2 git
git clone https://github.com/RUCbookshelf/mubi-reviewflow.git
cd mubi-reviewflow
```

`libcairo2` 用于 Linux 下的 SVG 渲染；缺少它可能导致部分图形导出异常。

### 2. 创建虚拟环境并安装依赖

```bash
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r build/requirements-app.txt
```

依赖文件是 `build/requirements-app.txt`；仓库根目录没有 `requirements.txt`。

### 3. 准备持久数据目录

```bash
mkdir -p /home/ubuntu/reviewflow-data
```

服务器模式要求 `COBOOKSHELF_DATA_DIR` 使用绝对路径。账户、任务数据库、导入原件、PDF 和筛选决定均保存到该目录。请定期备份整个目录。

### 4. 以服务器模式启动（团队协作必需）

**不要用 `build/launcher.py` 启动团队服务器。** 该启动入口会将运行模式设为桌面模式，不提供团队邀请和共享进度 API。服务器应直接通过 `build_app()` 启动完整的 API 和前端服务：

```bash
cd /home/ubuntu/mubi-reviewflow
REVIEWFLOW_MODE=server \
COBOOKSHELF_DATA_DIR=/home/ubuntu/reviewflow-data \
nohup .venv/bin/python -c "import sys; sys.path.insert(0, 'build'); from launcher import build_app; import uvicorn; uvicorn.run(build_app(), host='127.0.0.1', port=8613, log_level='warning')" \
> /home/ubuntu/reviewflow-data/server.out 2>&1 &
```

`REVIEWFLOW_MODE=server` 开启服务器团队协作；`build_app()` 同时提供 API 和前端。进程只监听 `127.0.0.1:8613`，由 Nginx 对外提供 HTTPS。生产环境建议使用 systemd 管理服务，以便开机自启和崩溃后恢复；上面的 `nohup` 命令适合验证部署，不会自动开机启动。

验证本机服务：

```bash
curl http://127.0.0.1:8613/api/health
```

### 5. 配置 Nginx HTTPS 反向代理

#### 场景 A：独立端口部署（推荐）

将域名或 IP、证书路径替换为实际值：

```nginx
server {
    listen 8443 ssl;
    server_name your-domain.example;

    ssl_certificate     /path/to/fullchain.pem;
    ssl_certificate_key /path/to/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;

    location / {
        proxy_pass http://127.0.0.1:8613;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 600s;
    }
}
```

#### 场景 B：共用 443 端口并挂载到子路径

前端静态资源可以使用相对路径，但页面 API 请求使用根路径 `/api/`，因此除了 `/reviewflow/` 页面代理外，还必须代理 `/api/`。在现有 443 `server` 块内加入以下 location，并按实际情况补充证书和公共代理设置：

```nginx
location = /reviewflow {
    return 301 /reviewflow/;
}

location ^~ /reviewflow/ {
    proxy_pass http://127.0.0.1:8613/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 600s;
}

location ^~ /api/ {
    proxy_pass http://127.0.0.1:8613;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 600s;
}
```

注意两个 `proxy_pass` 的尾部不同：页面代理带 `/`，会移除 `/reviewflow/` 前缀；API 代理不带尾部 `/`，会保留完整 `/api/...` 路径。若其他应用也使用 `/api/`，确认具体 API 前缀不冲突；Nginx 会优先采用更长的匹配前缀。

检查并重新加载 Nginx：

```bash
sudo nginx -t && sudo systemctl reload nginx
```

还需在云防火墙/安全组放行所用 HTTPS 端口（场景 A 示例为 8443）。不要对公网开放应用的 8613 端口。

### 6. 开启团队协作

1. 每位成员在服务器页面自行注册账户：用户名 3–64 个字符，密码至少 8 位。系统不会发送邮件邀请或代建账户。
2. 任务所有者登录并创建任务。
3. 所有者在任务协作设置中使用成员用户名发出邀请并分配角色：`coder` 提交获授权的编码；`reconciler` 读取全部编码、处理共识并冻结共识。
4. 成员的个人决定和操作轨迹按账户隔离；参与者可查看团队进度。

### 常见问题

- 找不到 `requirements.txt`：请使用 `build/requirements-app.txt`。
- 页面可以打开但功能异常：检查子路径部署是否同时代理 `/api/`。
- 无法邀请成员或看不到团队功能：确认进程由 `REVIEWFLOW_MODE=server` 启动，而不是桌面启动入口。
- 服务器重启后服务没有运行：`nohup` 不会自启；生产环境请配置 systemd。
- 备份：保存整个数据目录（包括数据库、PDF、协作状态）；只备份表格不能完整恢复任务。

<a id="en"></a>
## English

This guide documents a team-collaboration deployment completed on Ubuntu 24.04. Ubuntu 20.04, 22.04, and 24.04 are listed as deployment targets; Python 3.12 is recommended. The host needs GitHub access, Nginx, at least 2 GB RAM, and 10 GB disk space. Check dependency compatibility for other OS or Python versions.

The commands use `/home/ubuntu` as an example; replace it with the actual account home and source location.

### 1. Install system packages and get the source

```bash
sudo apt-get update
sudo apt-get install -y python3-venv libcairo2 git
git clone https://github.com/RUCbookshelf/mubi-reviewflow.git
cd mubi-reviewflow
```

`libcairo2` is used for SVG rendering on Linux; without it, some graphic exports may fail.

### 2. Create a virtual environment and install dependencies

```bash
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r build/requirements-app.txt
```

The dependency file is `build/requirements-app.txt`; there is no root-level `requirements.txt`.

### 3. Prepare a persistent data directory

```bash
mkdir -p /home/ubuntu/reviewflow-data
```

Server mode requires an absolute `COBOOKSHELF_DATA_DIR`. Accounts, task databases, imported originals, PDFs, and screening decisions are stored there. Back up the entire directory regularly.

### 4. Start in server mode (required for team collaboration)

**Do not start the team server with `build/launcher.py`.** That entry point forces desktop mode, which does not provide team invitations or shared-progress APIs. Start the complete API and frontend service directly through `build_app()`:

```bash
cd /home/ubuntu/mubi-reviewflow
REVIEWFLOW_MODE=server \
COBOOKSHELF_DATA_DIR=/home/ubuntu/reviewflow-data \
nohup .venv/bin/python -c "import sys; sys.path.insert(0, 'build'); from launcher import build_app; import uvicorn; uvicorn.run(build_app(), host='127.0.0.1', port=8613, log_level='warning')" \
> /home/ubuntu/reviewflow-data/server.out 2>&1 &
```

`REVIEWFLOW_MODE=server` enables team collaboration. `build_app()` serves both API and frontend. The process listens only on `127.0.0.1:8613`; Nginx provides external HTTPS access. For production, manage the service with systemd for startup and crash recovery. The `nohup` command is for deployment verification and does not start automatically after reboot.

Verify the local service:

```bash
curl http://127.0.0.1:8613/api/health
```

### 5. Configure an Nginx HTTPS reverse proxy

#### Option A: a dedicated port (recommended)

Replace the hostname and certificate paths with your values:

```nginx
server {
    listen 8443 ssl;
    server_name your-domain.example;
    ssl_certificate     /path/to/fullchain.pem;
    ssl_certificate_key /path/to/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;

    location / {
        proxy_pass http://127.0.0.1:8613;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 600s;
    }
}
```

#### Option B: share port 443 under a path

The frontend uses relative static-resource paths, but API calls use the root path `/api/`. Therefore, proxy both `/reviewflow/` and `/api/` inside the existing HTTPS `server` block:

```nginx
location = /reviewflow { return 301 /reviewflow/; }

location ^~ /reviewflow/ {
    proxy_pass http://127.0.0.1:8613/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 600s;
}

location ^~ /api/ {
    proxy_pass http://127.0.0.1:8613;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 600s;
}
```

The page `proxy_pass` ends with `/`, removing the `/reviewflow/` prefix; the API target has no trailing slash, preserving `/api/...`. If another app also uses `/api/`, check for route conflicts. Nginx selects the more specific longer prefix.

Validate and reload Nginx with `sudo nginx -t && sudo systemctl reload nginx`. Open the chosen HTTPS port in the cloud firewall/security group (8443 in Option A). Do not expose port 8613 publicly.

### 6. Enable team collaboration

1. Each member registers on the server (username: 3–64 characters; password: at least 8 characters). The system does not send email invitations or create accounts for members.
2. The task owner signs in and creates a task.
3. In task collaboration settings, the owner invites members by server username and assigns roles: `coder` submits authorized coding; `reconciler` reads all coding, handles consensus, and freezes it.
4. Personal decisions and activity traces are isolated by account; participants can view team progress.

### Troubleshooting

- Missing `requirements.txt`: use `build/requirements-app.txt`.
- Page loads but features fail: for path deployment, verify the `/api/` proxy.
- Cannot invite members or see team features: confirm `REVIEWFLOW_MODE=server`; the desktop launcher forces desktop mode.
- Service is down after reboot: `nohup` does not enable startup; configure systemd for production.
- Backups: copy the entire data directory, including database, PDFs, and collaboration state. A spreadsheet-only backup cannot restore a complete task.

<a id="fr"></a>
## Français

Ce guide décrit un déploiement du mode équipe réalisé sur Ubuntu 24.04. Ubuntu 20.04/22.04/24.04 sont indiqués comme cibles ; Python 3.12 est recommandé. Prévoyez l’accès à GitHub, Nginx, au moins 2 Go de RAM et 10 Go de disque. Vérifiez la compatibilité des dépendances sur les autres versions.

Les commandes utilisent `/home/ubuntu` comme exemple ; remplacez ce chemin par le compte et l’emplacement réels.

### Installation et données

```bash
sudo apt-get update
sudo apt-get install -y python3-venv libcairo2 git
git clone https://github.com/RUCbookshelf/mubi-reviewflow.git
cd mubi-reviewflow
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r build/requirements-app.txt
mkdir -p /home/ubuntu/reviewflow-data
```

`libcairo2` sert au rendu SVG sous Linux. Le fichier de dépendances est `build/requirements-app.txt` (pas de `requirements.txt` à la racine). `COBOOKSHELF_DATA_DIR` doit être un chemin absolu ; sauvegardez tout ce répertoire, qui contient comptes, tâches, imports, PDF et décisions.

### Démarrer le mode serveur

N’utilisez pas `build/launcher.py` pour un serveur d’équipe : il force le mode bureau et désactive les invitations et la progression partagée. Lancez directement `build_app()` :

```bash
cd /home/ubuntu/mubi-reviewflow
REVIEWFLOW_MODE=server \
COBOOKSHELF_DATA_DIR=/home/ubuntu/reviewflow-data \
nohup .venv/bin/python -c "import sys; sys.path.insert(0, 'build'); from launcher import build_app; import uvicorn; uvicorn.run(build_app(), host='127.0.0.1', port=8613, log_level='warning')" \
> /home/ubuntu/reviewflow-data/server.out 2>&1 &
curl http://127.0.0.1:8613/api/health
```

Le service écoute uniquement sur `127.0.0.1:8613`; Nginx assure l’accès HTTPS. `nohup` ne redémarre pas au démarrage de la machine : utilisez systemd en production.

### Proxy HTTPS et équipe

Option recommandée : port indépendant 8443, proxy vers `http://127.0.0.1:8613`, avec certificats TLS, HTTP/1.1, en-têtes `Host`, `X-Real-IP`, `X-Forwarded-*`, `Upgrade` et délai 600 s. Testez et rechargez avec `sudo nginx -t && sudo systemctl reload nginx`, puis ouvrez le port HTTPS dans le pare-feu cloud. Pour un sous-chemin `/reviewflow/` sur 443, ajoutez une redirection `/reviewflow` vers `/reviewflow/`, un proxy de `/reviewflow/` vers `http://127.0.0.1:8613/` (barre finale) **et** un proxy `/api/` vers `http://127.0.0.1:8613` (sans barre finale). Les API utilisent le préfixe absolu `/api/` ; vérifiez les conflits avec d’autres applications.

Chaque membre s’inscrit sur le serveur (nom d’utilisateur 3–64 caractères, mot de passe ≥8), puis le propriétaire crée la tâche et invite les membres par nom d’utilisateur. `coder` soumet les codages autorisés ; `reconciler` lit tous les codages, traite et fige le consensus. Les décisions personnelles restent isolées. Pour les sauvegardes, conserver le répertoire entier ; pour les problèmes, vérifier le mode serveur, le proxy `/api/` et l’absence de systemd.

<a id="ru"></a>
## Русский

Руководство основано на развёртывании командного режима в Ubuntu 24.04. Целевые версии Ubuntu: 20.04/22.04/24.04; рекомендуется Python 3.12. Нужны доступ к GitHub, Nginx, не менее 2 ГБ ОЗУ и 10 ГБ диска. Для других версий проверьте совместимость зависимостей.

В командах используется пример `/home/ubuntu`; замените его фактическим домашним каталогом и путём к исходному коду.

### Установка и данные

```bash
sudo apt-get update
sudo apt-get install -y python3-venv libcairo2 git
git clone https://github.com/RUCbookshelf/mubi-reviewflow.git
cd mubi-reviewflow
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r build/requirements-app.txt
mkdir -p /home/ubuntu/reviewflow-data
```

`libcairo2` нужен для SVG в Linux. Файл зависимостей — `build/requirements-app.txt`, а не корневой `requirements.txt`. `COBOOKSHELF_DATA_DIR` должен быть абсолютным путём. Регулярно копируйте весь каталог данных с аккаунтами, БД, оригиналами, PDF и решениями.

### Запуск сервера команды

Не используйте `build/launcher.py`: он принудительно включает настольный режим без командных приглашений и общего прогресса. Запустите `build_app()` напрямую:

```bash
cd /home/ubuntu/mubi-reviewflow
REVIEWFLOW_MODE=server \
COBOOKSHELF_DATA_DIR=/home/ubuntu/reviewflow-data \
nohup .venv/bin/python -c "import sys; sys.path.insert(0, 'build'); from launcher import build_app; import uvicorn; uvicorn.run(build_app(), host='127.0.0.1', port=8613, log_level='warning')" \
> /home/ubuntu/reviewflow-data/server.out 2>&1 &
curl http://127.0.0.1:8613/api/health
```

Сервис слушает только `127.0.0.1:8613`; внешний HTTPS обеспечивает Nginx. `nohup` не запускает службу после перезагрузки — для production используйте systemd.

### HTTPS-прокси и команда

Рекомендуется отдельный порт 8443: настройте TLS-сертификаты и проксирование на `http://127.0.0.1:8613` с HTTP/1.1, заголовками Host, IP, Forwarded, Upgrade и тайм-аутом 600 с. Для общего порта 443 и пути `/reviewflow/` настройте редирект без слеша, прокси страницы на адрес с завершающим `/` и отдельный `/api/` прокси на адрес без завершающего `/`. Это важно: фронтенд вызывает абсолютный `/api/`. Не допускайте конфликтов префиксов других приложений. Проверьте и перезагрузите Nginx командой `sudo nginx -t && sudo systemctl reload nginx`; откройте HTTPS-порт в облачном брандмауэре, но не публикуйте 8613.

Каждый участник регистрируется на сервере (имя 3–64 символа, пароль от 8), владелец создаёт задачу и приглашает по имени пользователя. `coder` отправляет разрешённое кодирование; `reconciler` читает кодирование, ведёт и фиксирует консенсус. Личные решения изолированы. При сбоях проверьте абсолютный каталог данных, `REVIEWFLOW_MODE=server`, прокси `/api/` и systemd; резервируйте весь каталог.

<a id="es"></a>
## Español

Esta guía se basa en un despliegue de colaboración en Ubuntu 24.04. Ubuntu 20.04/22.04/24.04 son los destinos indicados; se recomienda Python 3.12. Se necesita acceso a GitHub, Nginx, al menos 2 GB de RAM y 10 GB de disco. Compruebe compatibilidad en otras versiones.

Las rutas usan `/home/ubuntu` como ejemplo; sustitúyalo por la cuenta y ubicación reales.

### Instalación y datos

```bash
sudo apt-get update
sudo apt-get install -y python3-venv libcairo2 git
git clone https://github.com/RUCbookshelf/mubi-reviewflow.git
cd mubi-reviewflow
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r build/requirements-app.txt
mkdir -p /home/ubuntu/reviewflow-data
```

`libcairo2` permite renderizar SVG en Linux. El archivo de dependencias es `build/requirements-app.txt`. `COBOOKSHELF_DATA_DIR` debe ser una ruta absoluta. Haga copias periódicas del directorio completo, que contiene cuentas, base de datos, originales, PDF y decisiones.

### Iniciar el modo servidor

No use `build/launcher.py` para el servidor de equipo: fuerza el modo escritorio y desactiva invitaciones y progreso compartido. Inicie directamente `build_app()`:

```bash
cd /home/ubuntu/mubi-reviewflow
REVIEWFLOW_MODE=server \
COBOOKSHELF_DATA_DIR=/home/ubuntu/reviewflow-data \
nohup .venv/bin/python -c "import sys; sys.path.insert(0, 'build'); from launcher import build_app; import uvicorn; uvicorn.run(build_app(), host='127.0.0.1', port=8613, log_level='warning')" \
> /home/ubuntu/reviewflow-data/server.out 2>&1 &
curl http://127.0.0.1:8613/api/health
```

Escucha solo en `127.0.0.1:8613`; Nginx ofrece HTTPS. `nohup` no inicia el servicio al reiniciar: use systemd en producción.

### Nginx y colaboración

Se recomienda un puerto HTTPS independiente, por ejemplo 8443, con certificados y proxy a `http://127.0.0.1:8613`, HTTP/1.1, encabezados Host/IP/Forwarded/Upgrade y timeout de 600 s. Para compartir 443 bajo `/reviewflow/`, configure la redirección `/reviewflow` y dos proxies: página hacia `http://127.0.0.1:8613/` (con barra final) y `/api/` hacia `http://127.0.0.1:8613` (sin barra final). La interfaz llama a `/api/` desde la raíz; compruebe conflictos con otras aplicaciones. Valide y recargue Nginx con `sudo nginx -t && sudo systemctl reload nginx`, abra el puerto HTTPS en el cortafuegos y no exponga 8613.

Cada miembro se registra en el servidor (usuario de 3–64 caracteres y contraseña de al menos 8); el propietario crea la tarea e invita por nombre de usuario. `coder` envía codificación autorizada y `reconciler` revisa toda la codificación, gestiona y congela el consenso. Las decisiones personales están aisladas. En caso de problemas, revise modo servidor, proxy `/api/`, rutas absolutas y systemd; respalde el directorio completo.

<a id="ja"></a>
## 日本語

このガイドは Ubuntu 24.04 で実施したチーム協業サーバーの構築に基づきます。Ubuntu 20.04/22.04/24.04 を対象とし、Python 3.12 を推奨します。GitHub 接続、Nginx、メモリ 2 GB 以上、ディスク 10 GB 以上を用意してください。他の環境では依存関係の互換性を確認してください。

コマンド中の `/home/ubuntu` は例です。実際のユーザー名とソース配置先に置き換えてください。

### インストールとデータ

```bash
sudo apt-get update
sudo apt-get install -y python3-venv libcairo2 git
git clone https://github.com/RUCbookshelf/mubi-reviewflow.git
cd mubi-reviewflow
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r build/requirements-app.txt
mkdir -p /home/ubuntu/reviewflow-data
```

`libcairo2` は Linux の SVG 描画に必要です。依存ファイルは `build/requirements-app.txt` です。`COBOOKSHELF_DATA_DIR` は絶対パスにしてください。アカウント、DB、原本、PDF、判定を含むデータディレクトリ全体を定期的にバックアップします。

### サーバーモードで起動

チームサーバーで `build/launcher.py` を使わないでください。デスクトップモードを強制し、チーム招待と共有進捗を利用できません。`build_app()` を直接起動します。

```bash
cd /home/ubuntu/mubi-reviewflow
REVIEWFLOW_MODE=server \
COBOOKSHELF_DATA_DIR=/home/ubuntu/reviewflow-data \
nohup .venv/bin/python -c "import sys; sys.path.insert(0, 'build'); from launcher import build_app; import uvicorn; uvicorn.run(build_app(), host='127.0.0.1', port=8613, log_level='warning')" \
> /home/ubuntu/reviewflow-data/server.out 2>&1 &
curl http://127.0.0.1:8613/api/health
```

サービスは `127.0.0.1:8613` のみで待ち受け、外部 HTTPS は Nginx が担当します。`nohup` は再起動後に自動起動しません。本番では systemd を設定してください。

### Nginx とチーム設定

独立 HTTPS ポート（例 8443）を推奨し、証明書を設定して `http://127.0.0.1:8613` にプロキシします。共有 443 の `/reviewflow/` 配下に置く場合、ページ用 location（`proxy_pass` の末尾に `/`）と、絶対パス API 用 `/api/` location（末尾の `/` なし）の両方が必要です。ページ側は `/reviewflow/` 接頭辞が除かれ、API 側は `/api/...` が保持されます。他アプリとの API 接頭辞の重複を確認してください。`sudo nginx -t && sudo systemctl reload nginx` で検証・再読込し、クラウド側で HTTPS ポートを許可します。8613 は公開しないでください。

メンバーは同じサーバーに登録します（ユーザー名 3〜64 文字、パスワード 8 文字以上）。所有者がタスクを作成し、ユーザー名で招待します。`coder` は許可されたコーディングを提出し、`reconciler` は全コーディングを読み合意を処理・確定します。個人判定はアカウントごとに分離されます。問題時は `REVIEWFLOW_MODE=server`、`/api/` プロキシ、systemd を確認し、データディレクトリ全体をバックアップしてください。

<a id="pt"></a>
## Português

Este guia documenta uma implantação colaborativa realizada no Ubuntu 24.04. Ubuntu 20.04/22.04/24.04 são os destinos indicados; recomenda-se Python 3.12. São necessários acesso ao GitHub, Nginx, pelo menos 2 GB de RAM e 10 GB de disco. Verifique a compatibilidade em outras versões.

As rotas usam `/home/ubuntu` como exemplo; substitua pelo usuário e local reais.

### Instalação e dados

```bash
sudo apt-get update
sudo apt-get install -y python3-venv libcairo2 git
git clone https://github.com/RUCbookshelf/mubi-reviewflow.git
cd mubi-reviewflow
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r build/requirements-app.txt
mkdir -p /home/ubuntu/reviewflow-data
```

`libcairo2` é usado para renderizar SVG no Linux. O arquivo de dependências é `build/requirements-app.txt`. `COBOOKSHELF_DATA_DIR` deve ser absoluto. Faça backup periódico do diretório completo com contas, banco, originais, PDFs e decisões.

### Iniciar em modo servidor

Não use `build/launcher.py` para o servidor de equipe: ele força o modo desktop, sem convites ou progresso compartilhado. Inicie diretamente `build_app()`:

```bash
cd /home/ubuntu/mubi-reviewflow
REVIEWFLOW_MODE=server \
COBOOKSHELF_DATA_DIR=/home/ubuntu/reviewflow-data \
nohup .venv/bin/python -c "import sys; sys.path.insert(0, 'build'); from launcher import build_app; import uvicorn; uvicorn.run(build_app(), host='127.0.0.1', port=8613, log_level='warning')" \
> /home/ubuntu/reviewflow-data/server.out 2>&1 &
curl http://127.0.0.1:8613/api/health
```

O serviço escuta somente em `127.0.0.1:8613`; o Nginx fornece HTTPS externo. `nohup` não inicia após reinicialização; use systemd em produção.

### Nginx e equipe

Prefira uma porta HTTPS dedicada, como 8443, com certificados e proxy para `http://127.0.0.1:8613`. Para compartilhar 443 sob `/reviewflow/`, configure o redirecionamento sem barra e dois locais: página com `proxy_pass http://127.0.0.1:8613/` (barra final) e API `/api/` com `proxy_pass http://127.0.0.1:8613` (sem barra final). A interface usa o caminho absoluto `/api/`; verifique conflitos com outros serviços. Teste/recarregue com `sudo nginx -t && sudo systemctl reload nginx`, libere o HTTPS no firewall e não exponha a porta 8613.

Cada membro registra-se no servidor (usuário 3–64 caracteres, senha com pelo menos 8); o proprietário cria a tarefa e convida pelo nome de usuário. `coder` envia codificação autorizada; `reconciler` lê toda a codificação, trata e congela o consenso. Decisões pessoais ficam isoladas. Em caso de erro, confira o modo servidor, o proxy `/api/`, o caminho absoluto e o systemd; faça backup de todo o diretório de dados.

<a id="de"></a>
## Deutsch

Diese Anleitung basiert auf einer Teaminstallation unter Ubuntu 24.04. Ubuntu 20.04/22.04/24.04 sind als Zielsysteme angegeben; Python 3.12 wird empfohlen. Erforderlich sind GitHub-Zugriff, Nginx, mindestens 2 GB RAM und 10 GB Speicher. Für andere Versionen Abhängigkeiten prüfen.

Die Befehle verwenden `/home/ubuntu` als Beispiel; durch das tatsächliche Benutzerverzeichnis und den Quellcodepfad ersetzen.

### Installation und Daten

```bash
sudo apt-get update
sudo apt-get install -y python3-venv libcairo2 git
git clone https://github.com/RUCbookshelf/mubi-reviewflow.git
cd mubi-reviewflow
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r build/requirements-app.txt
mkdir -p /home/ubuntu/reviewflow-data
```

`libcairo2` unterstützt SVG-Rendering unter Linux. Die Abhängigkeitsdatei ist `build/requirements-app.txt`. `COBOOKSHELF_DATA_DIR` muss ein absoluter Pfad sein. Das gesamte Verzeichnis mit Konten, Datenbank, Importoriginalen, PDFs und Entscheidungen regelmäßig sichern.

### Servermodus starten

`build/launcher.py` nicht für den Teamserver verwenden: Dieser Einstieg erzwingt den Desktopmodus ohne Team-Einladungen und gemeinsamen Fortschritt. Starten Sie `build_app()` direkt:

```bash
cd /home/ubuntu/mubi-reviewflow
REVIEWFLOW_MODE=server \
COBOOKSHELF_DATA_DIR=/home/ubuntu/reviewflow-data \
nohup .venv/bin/python -c "import sys; sys.path.insert(0, 'build'); from launcher import build_app; import uvicorn; uvicorn.run(build_app(), host='127.0.0.1', port=8613, log_level='warning')" \
> /home/ubuntu/reviewflow-data/server.out 2>&1 &
curl http://127.0.0.1:8613/api/health
```

Der Dienst lauscht nur auf `127.0.0.1:8613`; Nginx stellt HTTPS bereit. `nohup` startet nach einem Neustart nicht automatisch. Für Produktion systemd einrichten.

### Nginx und Zusammenarbeit

Empfohlen wird ein eigener HTTPS-Port, etwa 8443, mit Zertifikat und Proxy auf `http://127.0.0.1:8613`. Für einen Unterpfad `/reviewflow/` auf Port 443 benötigen Sie einen Seiten-Proxy mit abschließendem `/` in `proxy_pass` sowie einen separaten `/api/`-Proxy ohne abschließenden Schrägstrich. Die API-Aufrufe verwenden `/api/` ab der Domainwurzel; prüfen Sie daher Konflikte mit anderen Diensten. Mit `sudo nginx -t && sudo systemctl reload nginx` testen und laden; HTTPS-Port in der Cloud-Firewall freigeben, Port 8613 nicht öffentlich öffnen.

Mitglieder registrieren sich auf demselben Server (Benutzername 3–64 Zeichen, Passwort mindestens 8). Der Eigentümer erstellt die Aufgabe und lädt per Benutzername ein. `coder` übermittelt autorisierte Codierung; `reconciler` liest alle Codierungen, bearbeitet und fixiert den Konsens. Persönliche Entscheidungen bleiben getrennt. Bei Fehlern Servermodus, `/api/`-Proxy, absoluten Datenpfad und systemd prüfen. Vollständiges Datenverzeichnis sichern.

<a id="sr"></a>
## Српски

Ovo uputstvo je zasnovano na timskom postavljanju na Ubuntu 24.04. Navedeni ciljevi su Ubuntu 20.04/22.04/24.04; preporučuje se Python 3.12. Potrebni su pristup GitHub-u, Nginx, najmanje 2 GB RAM-a i 10 GB prostora. Na drugim verzijama proverite zavisnosti.

Putanja `/home/ubuntu` u komandama je primer; zamenite je stvarnim korisničkim direktorijumom i lokacijom izvornog koda.

### Instalacija i podaci

```bash
sudo apt-get update
sudo apt-get install -y python3-venv libcairo2 git
git clone https://github.com/RUCbookshelf/mubi-reviewflow.git
cd mubi-reviewflow
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r build/requirements-app.txt
mkdir -p /home/ubuntu/reviewflow-data
```

`libcairo2` služi za SVG prikaz u Linux-u. Datoteka zavisnosti je `build/requirements-app.txt`. `COBOOKSHELF_DATA_DIR` mora biti apsolutna putanja. Redovno čuvajte ceo direktorijum sa nalozima, bazom, originalima, PDF-ovima i odlukama.

### Pokretanje serverskog režima

Nemojte koristiti `build/launcher.py` za timski server: on forsira desktop režim bez timskih pozivnica i zajedničkog napretka. Pokrenite `build_app()` direktno:

```bash
cd /home/ubuntu/mubi-reviewflow
REVIEWFLOW_MODE=server \
COBOOKSHELF_DATA_DIR=/home/ubuntu/reviewflow-data \
nohup .venv/bin/python -c "import sys; sys.path.insert(0, 'build'); from launcher import build_app; import uvicorn; uvicorn.run(build_app(), host='127.0.0.1', port=8613, log_level='warning')" \
> /home/ubuntu/reviewflow-data/server.out 2>&1 &
curl http://127.0.0.1:8613/api/health
```

Servis sluša samo na `127.0.0.1:8613`; Nginx pruža HTTPS. `nohup` ne pokreće servis posle ponovnog pokretanja računara; za produkciju podesite systemd.

### Nginx i tim

Preporučuje se zaseban HTTPS port, npr. 8443, sa sertifikatima i proxy-jem ka `http://127.0.0.1:8613`. Za podputanju `/reviewflow/` na portu 443 potrebni su proxy stranice sa završnim `/` u `proxy_pass` i zaseban `/api/` proxy bez završnog `/`, jer interfejs poziva apsolutni `/api/`. Proverite sukobe putanja sa drugim aplikacijama. Testirajte i ponovo učitajte Nginx komandom `sudo nginx -t && sudo systemctl reload nginx`; dozvolite HTTPS port u cloud firewall-u, ali ne izlažite 8613.

Članovi se registruju na istom serveru (korisničko ime 3–64 znaka, lozinka najmanje 8). Vlasnik pravi zadatak i poziva ih po korisničkom imenu. `coder` šalje ovlašćeno kodiranje; `reconciler` čita kodiranja i obrađuje i zaključava konsenzus. Lične odluke su odvojene. Kod problema proverite serverski režim, `/api/` proxy, apsolutnu putanju podataka i systemd; pravite kopiju celog direktorijuma.

<a id="ko"></a>
## 한국어

이 가이드는 Ubuntu 24.04 팀 협업 서버의 실제 배포를 바탕으로 합니다. Ubuntu 20.04/22.04/24.04를 대상 환경으로 안내하며 Python 3.12를 권장합니다. GitHub 접속, Nginx, 메모리 2GB 이상, 디스크 10GB 이상이 필요합니다. 다른 버전에서는 의존성 호환성을 확인하세요.

명령의 `/home/ubuntu`는 예시 경로입니다. 실제 사용자 홈과 소스 경로로 바꾸세요.

### 설치 및 데이터

```bash
sudo apt-get update
sudo apt-get install -y python3-venv libcairo2 git
git clone https://github.com/RUCbookshelf/mubi-reviewflow.git
cd mubi-reviewflow
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r build/requirements-app.txt
mkdir -p /home/ubuntu/reviewflow-data
```

`libcairo2`는 Linux SVG 렌더링에 사용됩니다. 의존성 파일은 루트의 `requirements.txt`가 아니라 `build/requirements-app.txt`입니다. `COBOOKSHELF_DATA_DIR`는 절대 경로여야 합니다. 계정, 작업 DB, 원본, PDF, 판정이 든 데이터 디렉터리 전체를 정기 백업하세요.

### 서버 모드 시작

팀 서버에서 `build/launcher.py`를 실행하지 마세요. 데스크톱 모드를 강제해 팀 초대와 공유 진행 기능을 제공하지 않습니다. `build_app()`을 직접 실행합니다.

```bash
cd /home/ubuntu/mubi-reviewflow
REVIEWFLOW_MODE=server \
COBOOKSHELF_DATA_DIR=/home/ubuntu/reviewflow-data \
nohup .venv/bin/python -c "import sys; sys.path.insert(0, 'build'); from launcher import build_app; import uvicorn; uvicorn.run(build_app(), host='127.0.0.1', port=8613, log_level='warning')" \
> /home/ubuntu/reviewflow-data/server.out 2>&1 &
curl http://127.0.0.1:8613/api/health
```

서비스는 `127.0.0.1:8613`에서만 대기하고 외부 HTTPS는 Nginx가 처리합니다. `nohup`은 재부팅 후 자동 실행되지 않으므로 운영 환경에서는 systemd를 사용하세요.

### Nginx 및 팀 협업

인증서를 설정한 별도 HTTPS 포트(예: 8443)를 권장하며 `http://127.0.0.1:8613`으로 프록시합니다. 443 포트에서 `/reviewflow/` 하위 경로를 공유할 경우 페이지 경로를 위한 프록시(`proxy_pass` 끝에 `/`)와 절대 API 경로 `/api/`를 위한 별도 프록시(끝에 `/` 없음)가 모두 필요합니다. 다른 앱의 API 경로와 충돌하지 않는지 확인하세요. `sudo nginx -t && sudo systemctl reload nginx`로 검사·재로드하고 클라우드 방화벽에서 HTTPS를 허용하되 8613은 외부에 열지 마세요.

모든 구성원은 같은 서버에서 가입해야 합니다(사용자명 3~64자, 비밀번호 8자 이상). 소유자가 작업을 만들고 사용자명으로 초대합니다. `coder`는 승인된 코딩을 제출하고 `reconciler`는 전체 코딩을 읽어 합의를 처리·확정합니다. 개인 판정은 계정별로 분리됩니다. 문제가 있으면 서버 모드, `/api/` 프록시, 절대 데이터 경로, systemd를 확인하고 전체 데이터 디렉터리를 백업하세요.
