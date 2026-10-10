# Server Collaboration and Offline Merge Guide

[中文](#zh-cn) · [English](#en) · [Français](#fr) · [Русский](#ru) · [Español](#es) · [日本語](#ja) · [Português](#pt) · [Deutsch](#de) · [Српски](#sr) · [한국어](#ko)

<a id="zh-cn"></a>
## 中文

ReviewFlow 的服务器协作适用于同一受控服务器上的多个账户共同完成任务。它依赖服务器模式；桌面本地模式不会开放团队邀请和团队进度 API。Ubuntu 部署步骤见[服务器部署指南](../deployment/UBUNTU_SERVER.md#zh-cn)。

## 1. 部署服务器

按[服务器部署指南](../deployment/UBUNTU_SERVER.md#zh-cn)设置持久、可写的数据目录，并在受控主机上运行服务。对外访问应由 HTTPS 反向代理保护；不要将开发服务器直接暴露到公网。限制服务器和数据目录的系统账户访问，安排数据库及附件备份，并定期验证恢复流程。

服务器中的账户、任务数据库、导入原件、PDF 和筛选决定保存在服务器数据目录。服务器管理员应按组织的数据管理要求设置访问控制、备份周期和保留期限。在线 AI 功能还会将相应请求发送到用户配置的服务。

## 2. 创建任务并授权成员

1. 任务所有者登录服务器，创建任务。
2. 成员必须先在同一服务器注册自己的账户。邀请使用其服务器用户名；本功能不会替用户创建账户，也不会发送邮件邀请。
3. 所有者在任务的协作设置中邀请成员并分配角色。只有任务所有者可以授予或撤销角色、查看成员授权清单。
4. 角色为任务级权限：
   - **编码员（coder）**：提交获授权的编码内容。
   - **合并协调员（reconciler）**：读取合并所需的全部编码、处理共识记录并冻结编码共识。
   - 任务所有者拥有该任务的协作操作权限。
5. 参与者可查看团队任务进度。授权仅适用于对应任务；移除角色后，成员不再拥有该角色所需的协作操作权限。

现有未登记所有者的旧任务不会仅因有人访问就自动归属某个账户。遇到未登记任务时，请使用受支持的迁移/管理流程，不要直接修改协作数据库。

## 3. 分开完成判断并核对共识

成员使用自己的账户完成分配的编码或筛选工作。个人筛选决定和个人轨迹按账户隔离。角色授予不表示一名成员可以改写另一名成员的个人决定。

协调员应检查编码差异和共识记录，按研究方案处理分歧，并在冻结前确认记录和共识状态。冻结后仍应保留研究团队认可的裁决依据。不要仅凭汇总进度代替对差异的核对。

## 4. 离线工作与导入合并

若需离线完成决策，在客户端按应用提供的决策导出功能导出文件；成员应分别保存各自导出的原件，并注明任务、阶段、导出账户和时间。在线时使用合并/仲裁页面导入相应决策包，查看冲突，再由有权限的人员作出裁决。

合并前：

- 确认所有文件属于同一任务和筛选阶段，并检查其来源成员。
- 保留每份导出文件的原件，避免编辑列名、条目标识符或决定字段。
- 检查冲突、缺失条目和重复导入提示；不要假定导入即代表所有分歧都已解决。
- 由团队按预先约定的规则解决分歧，并记录裁决人、理由和日期。

本地与服务器协作功能会随版本演进；以当前版本界面、API 权限提示和导入报告为准。遇到权限错误时，请由任务所有者核对账户、任务角色和服务器模式，不要通过共享密码绕过权限控制。

## 5. 备份、隐私与帮助

服务器管理员应备份整个 ReviewFlow 数据目录，包含任务数据库、PDF 和协作状态；只备份某个表格可能无法恢复完整任务。备份应存放在受控位置，并定期做恢复演练。

报告问题时附应用版本、服务器/客户端模式、任务阶段和去除个人信息后的错误信息。不要上传账户凭据、个人筛选数据或未获授权的全文。

<a id="en"></a>
## English

ReviewFlow server collaboration lets multiple accounts work on tasks hosted by the same controlled server. It requires server mode; the local desktop mode does not expose team invitations or team-progress APIs. See the [Ubuntu deployment guide](../deployment/UBUNTU_SERVER.md#en) for deployment steps.

### 1. Deploy the server
Configure a persistent, writable data directory and run the service on a controlled host. Protect external access with an HTTPS reverse proxy; do not expose the development server directly to the public internet. Restrict OS access to the service and data directory, back up the database and attachments, and test restores. See the [Ubuntu deployment guide](../deployment/UBUNTU_SERVER.md#en) for the verified setup.

Accounts, task databases, imported originals, PDFs, and screening decisions are stored in the server data directory. Administrators should set access, backup, and retention controls under their organization’s policy. Online AI requests are sent to the service configured by the user.

### 2. Create a task and invite members
1. The owner signs in to the server and creates a task.
2. Members first register on that same server. Invitations use their server username; the feature does not create accounts or send email.
3. The owner invites members in task collaboration settings and assigns roles. Only the owner can grant/revoke roles and view the authorization list.
4. Roles are task-specific: **coder** submits authorized coding; **reconciler** reads all coding needed for reconciliation, handles consensus records, and freezes coding consensus. The owner has collaboration permissions for the task.
5. Participants can see team progress. Removing a role removes the collaboration permissions associated with it.

Legacy tasks without a registered owner are not automatically assigned to whoever opens them. Use a supported migration/administration process; do not edit the collaboration database directly.

### 3. Work independently and reconcile
Members use their own accounts for assigned coding or screening. Personal screening decisions and traces are isolated by account; a role does not authorize rewriting another member’s personal decisions. The reconciler reviews differences and consensus records, follows the protocol, and verifies the record and consensus state before freezing. Do not rely on aggregate progress alone.

### 4. Offline work and merge
For offline decisions, export through the app’s decision-export function. Keep each member’s original export and record task, stage, account, and time. When online, import the decision packages through the merge/adjudication page, inspect conflicts, and let an authorized person resolve them.

Before merging, confirm all files belong to the same task and screening stage and check their source; preserve unmodified originals; inspect conflicts, missing items, and duplicate-import notices; and record the adjudicator, rationale, and date according to the team’s agreed rules. An import does not mean disagreements are resolved.

Features evolve. Follow the current UI, API permission messages, and import report. For permission errors, have the owner check account, task role, and server mode; do not share passwords to bypass access control.

### 5. Backup, privacy, and help
Back up the entire ReviewFlow data directory, including task database, PDFs, and collaboration state. A spreadsheet-only backup may not restore a task. Store backups in a controlled location and test recovery. Bug reports should include app version, server/client mode, task stage, and sanitized error details. Do not upload credentials, personal screening data, or unauthorized full text.

<a id="fr"></a>
## Français

La collaboration serveur permet à plusieurs comptes de travailler sur des tâches hébergées par le même serveur contrôlé. Elle nécessite le mode serveur ; le mode local ne fournit pas les invitations d’équipe ni les API de progression collective. Consultez le [guide de déploiement Ubuntu](../deployment/UBUNTU_SERVER.md#fr) pour la procédure.

### Déploiement et comptes
Configurez un répertoire de données persistant et accessible en écriture sur un hôte contrôlé. Protégez l’accès externe par un proxy HTTPS et n’exposez pas directement le serveur de développement. Limitez les accès système, sauvegardez base et pièces jointes, puis testez la restauration. Les comptes, tâches, originaux importés, PDF et décisions sont stockés sur le serveur. L’administrateur définit accès, sauvegarde et conservation. Les requêtes IA en ligne sont envoyées au service configuré par l’utilisateur.

Le propriétaire crée la tâche ; les membres s’inscrivent d’abord sur le même serveur. L’invitation utilise le nom d’utilisateur serveur et ne crée pas de compte ni n’envoie de courriel. Le propriétaire attribue les rôles : **coder** soumet le codage autorisé ; **reconciler** lit le codage nécessaire, traite le consensus et le fige. Les rôles sont propres à la tâche. Les anciennes tâches sans propriétaire enregistré ne sont pas automatiquement attribuées.

### Travail, fusion et sauvegarde
Chaque membre travaille avec son compte ; ses décisions et traces personnelles restent isolées. Le rôle n’autorise pas à modifier celles d’autrui. Le coordinateur examine les divergences et le consensus avant gel.

Pour travailler hors ligne, exportez les décisions dans l’application, conservez les originaux et notez tâche, étape, compte et date. À la reconnexion, importez les paquets dans la page de fusion/arbitrage, vérifiez leur provenance, les conflits, éléments manquants et doublons, puis faites trancher par une personne autorisée. L’import ne signifie pas que les désaccords sont réglés ; consignez la décision, son motif et sa date. Sauvegardez le répertoire ReviewFlow entier, y compris base, PDF et état collaboratif, et testez la restauration. Les rapports doivent exclure identifiants, données personnelles et textes non autorisés.

<a id="ru"></a>
## Русский

Совместная работа ReviewFlow позволяет нескольким аккаунтам работать над задачами на одном контролируемом сервере. Нужен серверный режим; локальная настольная версия не предоставляет приглашения команды и API общего прогресса.

### Развёртывание и доступ
Настройте постоянный каталог данных с правом записи на контролируемом хосте. Защитите внешний доступ HTTPS-прокси и не открывайте напрямую сервер разработки. Ограничьте системный доступ, резервируйте БД и вложения, проверяйте восстановление. Аккаунты, задачи, исходные импорты, PDF и решения хранятся в серверном каталоге. Администратор задаёт доступ, резервирование и сроки хранения. Онлайн-запросы ИИ отправляются в сервис, настроенный пользователем. Проверенная процедура установки приведена в [руководстве для Ubuntu](../deployment/UBUNTU_SERVER.md#ru).

Владелец создаёт задачу; участники сначала регистрируются на том же сервере. Приглашение использует серверное имя пользователя, не создаёт аккаунт и не отправляет письмо. Владелец назначает роли: **coder** отправляет разрешённое кодирование; **reconciler** читает необходимые данные, обрабатывает консенсус и фиксирует его. Роли действуют только для задачи. Старые задачи без владельца не назначаются автоматически.

### Работа, объединение и резервирование
Каждый участник использует собственный аккаунт; личные решения и история изолированы. Роль не даёт права менять решения других. Координатор проверяет различия и консенсус до фиксации.

Для автономной работы экспортируйте решения через приложение, сохраните оригиналы и укажите задачу, этап, аккаунт и время. После подключения загрузите пакеты на страницу объединения/арбитража, проверьте источники, конфликты, пропуски и повторный импорт; уполномоченный участник выносит решение. Сам импорт не разрешает разногласия — запишите арбитра, основание и дату. Резервируйте весь каталог ReviewFlow с БД, PDF и состоянием сотрудничества и проверяйте восстановление. Не передавайте учётные данные, личные решения или неразрешённые полные тексты.

<a id="es"></a>
## Español

La colaboración en servidor permite que varias cuentas trabajen en tareas alojadas en un mismo servidor controlado. Requiere el modo servidor; el modo local de escritorio no ofrece invitaciones de equipo ni API de progreso compartido. Consulte la [guía de despliegue de Ubuntu](../deployment/UBUNTU_SERVER.md#es) para ver los pasos.

### Servidor y miembros
Configure un directorio de datos persistente y escribible en un host controlado. Proteja el acceso externo con un proxy HTTPS; no exponga directamente el servidor de desarrollo. Restrinja el acceso al sistema, haga copias de la base y los adjuntos y pruebe la restauración. Cuentas, tareas, originales importados, PDF y decisiones se guardan en el servidor. El administrador define controles de acceso, copias y retención. Las solicitudes de IA en línea se envían al servicio configurado por el usuario.

El propietario crea la tarea y los miembros se registran primero en ese servidor. La invitación usa el nombre de usuario del servidor; no crea cuentas ni envía correos. El propietario asigna roles: **coder** envía codificación autorizada; **reconciler** consulta la codificación necesaria, gestiona el consenso y lo congela. Los permisos son por tarea. Las tareas antiguas sin propietario no se asignan automáticamente.

### Trabajo, combinación y copias
Cada miembro usa su cuenta; sus decisiones y trazas personales están aisladas. El rol no permite modificar las decisiones personales de otra persona. El coordinador revisa discrepancias y consenso antes de congelarlo.

Para trabajar sin conexión, exporte decisiones desde la aplicación, conserve los archivos originales y anote tarea, fase, cuenta y fecha. Al volver, importe los paquetes en la página de combinación/arbitraje, revise origen, conflictos, faltantes y duplicados, y deje que una persona autorizada resuelva las diferencias. Importar no significa que el conflicto esté resuelto: registre quién decidió, motivo y fecha. Haga copia de todo el directorio de datos, incluidos DB, PDF y estado colaborativo, y pruebe la recuperación. No comparta credenciales, datos personales ni textos no autorizados.

<a id="ja"></a>
## 日本語

サーバー協業では、同一の管理されたサーバー上の複数アカウントでタスクを進められます。サーバーモードが必要です。デスクトップのローカルモードではチーム招待と共有進捗 API は利用できません。構築手順は[Ubuntu サーバー導入ガイド](../deployment/UBUNTU_SERVER.md#ja)を参照してください。

### サーバー構築とメンバー
管理されたホストに永続的で書き込み可能なデータディレクトリを設定し、外部アクセスは HTTPS リバースプロキシで保護してください。開発サーバーを直接公開しないでください。OS 権限を制限し、DB と添付ファイルをバックアップして復元を試します。アカウント、タスク、インポート原本、PDF、判定はサーバーに保存されます。管理者がアクセス、保存期間、バックアップを管理します。オンライン AI リクエストはユーザーが設定したサービスに送信されます。

所有者がタスクを作成し、メンバーは同じサーバーで先に登録します。招待はサーバーのユーザー名を使い、アカウント作成やメール送信は行いません。所有者がタスク単位で役割を割り当てます。**coder** は許可されたコーディングを送信し、**reconciler** は必要な全コーディングを読み、合意記録を処理して確定します。所有者未登録の旧タスクは自動割り当てされません。

### 作業、オフライン統合、バックアップ
各メンバーは自分のアカウントで作業し、個人の判定と履歴は分離されます。他のメンバーの判定を書き換える権限はありません。調整担当者は差異と合意状態を確認してから確定します。

オフライン作業ではアプリの判定エクスポートを使い、原本を保管し、タスク・段階・アカウント・日時を記録します。オンライン復帰後、統合/調停画面でインポートし、出所、競合、欠落、重複を確認して権限者が判断します。インポートだけで意見の相違は解決しません。判断者、理由、日付を記録してください。DB、PDF、協業状態を含むデータディレクトリ全体をバックアップし、復元を試します。認証情報や個人データ、未許諾全文を共有しないでください。

<a id="pt"></a>
## Português

A colaboração em servidor permite que várias contas trabalhem em tarefas hospedadas no mesmo servidor controlado. É necessário usar o modo servidor; o modo local de desktop não oferece convites de equipe nem APIs de progresso compartilhado. Consulte o [guia de implantação Ubuntu](../deployment/UBUNTU_SERVER.md#pt) para seguir os passos.

### Implantação e membros
Configure um diretório persistente e gravável em um host controlado. Proteja o acesso externo com proxy HTTPS e não exponha diretamente o servidor de desenvolvimento. Restrinja o acesso ao sistema, faça backup do banco e anexos e teste a restauração. Contas, tarefas, originais importados, PDFs e decisões ficam no servidor. O administrador define acesso, retenção e backups. Solicitações de IA online são enviadas ao serviço escolhido pelo usuário.

O proprietário cria a tarefa; os membros devem registrar-se no mesmo servidor. O convite usa o nome de usuário local do servidor e não cria contas nem envia e-mail. O proprietário atribui funções por tarefa: **coder** envia codificação autorizada; **reconciler** lê a codificação necessária, trata o consenso e o congela. Tarefas antigas sem proprietário registrado não são atribuídas automaticamente.

### Trabalho, mesclagem e backup
Cada pessoa usa sua própria conta; decisões e trilhas pessoais ficam isoladas. A função não permite alterar decisões de outra pessoa. O reconciliador verifica divergências e consenso antes de congelar.

Para trabalhar offline, exporte decisões pelo aplicativo, preserve os originais e registre tarefa, etapa, conta e horário. Depois importe os pacotes na página de mesclagem/arbitragem, confira origem, conflitos, itens ausentes e duplicados e peça a uma pessoa autorizada que decida. A importação não resolve automaticamente divergências; registre responsável, motivo e data. Faça backup do diretório completo, incluindo banco, PDFs e estado colaborativo, e teste a recuperação. Não compartilhe credenciais, dados pessoais ou textos não autorizados.

<a id="de"></a>
## Deutsch

Die Server-Kollaboration ermöglicht mehreren Konten, Aufgaben auf demselben kontrollierten Server zu bearbeiten. Sie setzt den Servermodus voraus; der lokale Desktopmodus bietet keine Teameinladungen oder APIs für gemeinsamen Fortschritt. Die Schritte stehen im [Ubuntu-Serverleitfaden](../deployment/UBUNTU_SERVER.md#de).

### Server und Mitglieder
Richten Sie auf einem kontrollierten Host ein dauerhaftes, beschreibbares Datenverzeichnis ein. Schützen Sie externen Zugriff durch einen HTTPS-Reverse-Proxy und stellen Sie den Entwicklungsserver nicht direkt ins Internet. Beschränken Sie Systemzugriffe, sichern Sie Datenbank und Anhänge und testen Sie die Wiederherstellung. Konten, Aufgaben, Importoriginale, PDFs und Entscheidungen liegen im Serververzeichnis. Administratoren legen Zugriffe, Sicherung und Aufbewahrung fest. Online-KI-Anfragen gehen an den vom Nutzer konfigurierten Dienst.

Der Eigentümer erstellt die Aufgabe; Mitglieder registrieren sich zuerst auf demselben Server. Einladungen verwenden den Server-Benutzernamen und erstellen weder Konten noch E-Mails. Rollen werden pro Aufgabe vergeben: **coder** übermittelt autorisierte Codierung; **reconciler** liest benötigte Codierungen, bearbeitet den Konsens und friert ihn ein. Alte Aufgaben ohne registrierten Eigentümer werden nicht automatisch zugeordnet.

### Arbeit, Zusammenführung und Sicherung
Jedes Mitglied nutzt ein eigenes Konto; persönliche Entscheidungen und Verläufe bleiben getrennt. Eine Rolle erlaubt nicht, Entscheidungen anderer zu ändern. Der Koordinator prüft Unterschiede und Konsens vor dem Einfrieren.

Für Offline-Arbeit Entscheidungen in der App exportieren, Originale aufbewahren und Aufgabe, Phase, Konto und Zeitpunkt notieren. Nach der Rückkehr Pakete auf der Zusammenführungs-/Schlichtungsseite importieren, Herkunft, Konflikte, fehlende Einträge und Duplikate prüfen und durch eine berechtigte Person entscheiden lassen. Der Import löst Differenzen nicht automatisch; Entscheider, Grund und Datum dokumentieren. Das gesamte Datenverzeichnis einschließlich Datenbank, PDFs und Kollaborationsstatus sichern und Wiederherstellung testen. Keine Zugangsdaten, personenbezogenen Entscheidungen oder nicht autorisierten Volltexte teilen.

<a id="sr"></a>
## Српски

Serverska saradnja omogućava da više naloga radi na zadacima na istom kontrolisanom serveru. Potreban je serverski režim; lokalna desktop verzija ne pruža timske pozivnice ni API za zajednički napredak. Postupak postavljanja opisan je u [Ubuntu vodiču za server](../deployment/UBUNTU_SERVER.md#sr).

### Server i članovi
Podesite trajan direktorijum podataka sa dozvolom za upis na kontrolisanom računaru. Spoljni pristup zaštitite HTTPS posredničkim serverom; razvojni server ne izlažite direktno internetu. Ograničite sistemski pristup, pravite rezervne kopije baze i priloga i proveravajte vraćanje. Nalozi, zadaci, originalni uvozi, PDF-ovi i odluke čuvaju se na serveru. Administrator uređuje pristup, rokove čuvanja i kopije. Online AI zahtevi šalju se servisu koji je korisnik podesio.

Vlasnik pravi zadatak, a članovi se prvo registruju na istom serveru. Poziv koristi serversko korisničko ime; ne pravi nalog i ne šalje e-poštu. Vlasnik dodeljuje uloge za zadatak: **coder** šalje ovlašćeno kodiranje; **reconciler** čita potrebno kodiranje, obrađuje konsenzus i zaključava ga. Stari zadaci bez registrovanog vlasnika ne dodeljuju se automatski.

### Rad, spajanje i kopije
Svaki član koristi svoj nalog; lične odluke i tragovi su odvojeni. Uloga ne dozvoljava menjanje tuđih odluka. Koordinator proverava razlike i konsenzus pre zaključavanja.

Za rad van mreže izvezite odluke iz aplikacije, sačuvajte originale i zabeležite zadatak, fazu, nalog i vreme. Po povratku uvezite pakete na stranici za spajanje/arbitražu, proverite izvor, sukobe, nedostajuće stavke i duplikate; ovlašćena osoba odlučuje. Uvoz sam po sebi ne rešava neslaganja — zabeležite ko je odlučio, razlog i datum. Pravite kopiju celog direktorijuma, uključujući bazu, PDF-ove i stanje saradnje, i testirajte obnovu. Ne delite lozinke, lične odluke ni neovlašćene pune tekstove.

<a id="ko"></a>
## 한국어

서버 협업은 동일한 관리 서버에 있는 여러 계정이 작업을 함께 수행하도록 합니다. 서버 모드가 필요하며 데스크톱 로컬 모드는 팀 초대 및 공동 진행 API를 제공하지 않습니다. 배포 절차는 [Ubuntu 서버 배포 안내](../deployment/UBUNTU_SERVER.md#ko)를 참고하세요.

### 서버 배포 및 구성원 초대
관리되는 호스트에 영구적이고 쓰기 가능한 데이터 디렉터리를 설정하세요. 외부 접속은 HTTPS 역방향 프록시로 보호하고 개발 서버를 인터넷에 직접 노출하지 마세요. 시스템 접근을 제한하고 DB와 첨부파일을 백업하며 복구를 시험하세요. 계정, 작업 DB, 가져온 원본, PDF 및 판정은 서버 데이터 디렉터리에 저장됩니다. 관리자는 접근 권한, 백업 주기와 보존 기간을 정합니다. 온라인 AI 요청은 사용자가 설정한 서비스로 전송됩니다.

소유자가 서버에서 작업을 만들고 구성원은 같은 서버에 먼저 가입해야 합니다. 초대는 서버 사용자 이름을 사용하며 계정을 만들거나 이메일을 보내지 않습니다. 소유자가 작업별 역할을 부여합니다. **coder**는 허가된 코딩을 제출하고 **reconciler**는 필요한 코딩을 읽고 합의 기록을 처리해 확정합니다. 등록된 소유자가 없는 기존 작업은 자동으로 계정에 귀속되지 않습니다.

### 개별 작업, 오프라인 병합 및 백업
구성원은 각자 계정을 사용하며 개인 판정과 이력은 분리됩니다. 역할이 다른 사람의 판정을 수정할 권한을 주지는 않습니다. 조정자는 확정 전에 차이와 합의 기록을 확인합니다.

오프라인 작업은 앱의 판정 내보내기를 사용하고 원본을 보존하며 작업, 단계, 계정, 시간을 기록하세요. 온라인 복귀 후 병합/조정 화면에서 파일을 가져오고 출처, 충돌, 누락, 중복을 확인한 뒤 권한자가 판단합니다. 가져오기만으로 의견 차이가 해결되지는 않습니다. 결정자, 근거, 날짜를 기록하세요. DB, PDF, 협업 상태가 포함된 데이터 디렉터리 전체를 백업하고 복구를 시험하세요. 계정 정보, 개인 판정 자료, 무단 원문을 공유하지 마세요.
