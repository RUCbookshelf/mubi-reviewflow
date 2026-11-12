(function () {
  'use strict';
  if (typeof RFLang === 'undefined') return;
  RFLang.register([
    ["厂商","Provider","Fournisseur","Поставщик","Proveedor","プロバイダー","Provedor","Anbieter","Провајдер","공급자"],
    ["请选择厂商","Select a provider","Choisir un fournisseur","Выберите поставщика","Seleccione un proveedor","プロバイダーを選択","Selecione um provedor","Anbieter auswählen","Изаберите провајдера","공급자를 선택하세요"],
    ["自定义…","Custom…","Personnalisé…","Другой…","Personalizado…","カスタム…","Personalizado…","Benutzerdefiniert…","Прилагођено…","사용자 지정…"],
    ["选择 API 格式","Choose API format","Choisir le format d’API","Выберите формат API","Elegir formato de API","API 形式を選択","Escolher formato da API","API-Format auswählen","Изаберите API формат","API 형식 선택"],
    ["后端暂不支持此格式","Server does not support this format","Le serveur ne prend pas ce format en charge","Сервер не поддерживает этот формат","El servidor no admite este formato","サーバーはこの形式に未対応です","O servidor não aceita este formato","Server unterstützt dieses Format nicht","Сервер не подржава овај формат","서버에서 지원하지 않는 형식"],
    ["仅桌面版","Desktop only","Version bureau uniquement","Только настольная версия","Solo escritorio","デスクトップ版のみ","Apenas no desktop","Nur Desktop-Version","Само десктоп верзија","데스크톱 전용"],
    ["测试中…","Testing…","Test en cours…","Проверка…","Probando…","テスト中…","Testando…","Test läuft…","Тестирање…","테스트 중…"],
    ["管理员配置","Managed by administrator","Géré par l’administrateur","Настроено администратором","Configurado por el administrador","管理者が設定","Configurado pelo administrador","Vom Administrator verwaltet","Подесио администратор","관리자 설정"],
    ["调用 {0} 次 · 累计 {1} tokens","{0} calls · {1} total tokens","{0} appels · {1} jetons au total","Вызовы: {0} · всего токенов: {1}","{0} llamadas · {1} tokens en total","呼び出し {0} 回 · 合計 {1} トークン","{0} chamadas · {1} tokens no total","{0} Aufrufe · {1} Token insgesamt","Позиви: {0} · укупно токена: {1}","호출 {0}회 · 누적 토큰 {1}개"],
    ["最近使用 {0}","Last used {0}","Dernière utilisation {0}","Последнее использование: {0}","Último uso: {0}","最終使用 {0}","Último uso: {0}","Zuletzt verwendet: {0}","Последња употреба: {0}","최근 사용: {0}"],
    ["最近 {0} 次调用失败，请检查钥匙或额度","The last {0} calls failed. Check the key or quota.","Les {0} derniers appels ont échoué. Vérifiez la clé ou le quota.","Последние {0} вызовов завершились ошибкой. Проверьте ключ или квоту.","Fallaron las últimas {0} llamadas. Revise la clave o la cuota.","直近 {0} 回の呼び出しに失敗しました。キーまたは利用枠を確認してください。","As últimas {0} chamadas falharam. Verifique a chave ou a cota.","Die letzten {0} Aufrufe sind fehlgeschlagen. Prüfen Sie Schlüssel oder Kontingent.","Последњих {0} позива није успело. Проверите кључ или квоту.","최근 호출 {0}회가 실패했습니다. 키 또는 할당량을 확인하세요."],
    ["通用 LLM 钥匙","General LLM key","Clé LLM générale","Ключ общего доступа к LLM","Clave LLM general","汎用 LLM キー","Chave LLM geral","Allgemeiner LLM-Schlüssel","Општи LLM кључ","범용 LLM 키"],
    ["专用判定钥匙","Decision service key","Clé du service de décision","Ключ сервиса решений","Clave del servicio de decisión","判定サービスのキー","Chave do serviço de decisão","Schlüssel für den Entscheidungsdienst","Кључ сервиса одлучивања","판정 서비스 키"],
    ["已保存，可测试连接。","Saved. You can test the connection.","Enregistré. Vous pouvez tester la connexion.","Сохранено. Можно проверить соединение.","Guardado. Puede probar la conexión.","保存しました。接続をテストできます。","Salvo. Você pode testar a conexão.","Gespeichert. Sie können die Verbindung testen.","Сачувано. Можете тестирати везу.","저장했습니다. 연결을 테스트할 수 있습니다."]
  ]);
  RFLang.register([
    Object.assign(["OpenAI","OpenAI","OpenAI","OpenAI","OpenAI","OpenAI","OpenAI","OpenAI","OpenAI","OpenAI"], { aliases:['ai_providers.openai'] }),
    Object.assign(["Anthropic","Anthropic","Anthropic","Anthropic","Anthropic","Anthropic","Anthropic","Anthropic","Anthropic","Anthropic"], { aliases:['ai_providers.anthropic'] }),
    Object.assign(["Google","Google","Google","Google","Google","Google","Google","Google","Google","Google"], { aliases:['ai_providers.google'] }),
    Object.assign(["DeepSeek","DeepSeek","DeepSeek","DeepSeek","DeepSeek","DeepSeek","DeepSeek","DeepSeek","DeepSeek","DeepSeek"], { aliases:['ai_providers.deepseek'] }),
    Object.assign(["Qwen","Qwen","Qwen","Qwen","Qwen","Qwen","Qwen","Qwen","Qwen","Qwen"], { aliases:['ai_providers.qwen'] }),
    Object.assign(["Zhipu","Zhipu","Zhipu","Zhipu","Zhipu","Zhipu","Zhipu","Zhipu","Zhipu","Zhipu"], { aliases:['ai_providers.zhipu'] }),
    Object.assign(["Moonshot AI","Moonshot AI","Moonshot AI","Moonshot AI","Moonshot AI","Moonshot AI","Moonshot AI","Moonshot AI","Moonshot AI","Moonshot AI"], { aliases:['ai_providers.moonshot'] }),
    Object.assign(["MiniMax","MiniMax","MiniMax","MiniMax","MiniMax","MiniMax","MiniMax","MiniMax","MiniMax","MiniMax"], { aliases:['ai_providers.minimax'] }),
    Object.assign(["xAI","xAI","xAI","xAI","xAI","xAI","xAI","xAI","xAI","xAI"], { aliases:['ai_providers.xai'] }),
    Object.assign(["Mistral AI","Mistral AI","Mistral AI","Mistral AI","Mistral AI","Mistral AI","Mistral AI","Mistral AI","Mistral AI","Mistral AI"], { aliases:['ai_providers.mistral'] }),
    Object.assign(["OpenCode Go","OpenCode Go","OpenCode Go","OpenCode Go","OpenCode Go","OpenCode Go","OpenCode Go","OpenCode Go","OpenCode Go","OpenCode Go"], { aliases:['ai_providers.opencode_go'] }),
    Object.assign(["CommandCode GOAT","CommandCode GOAT","CommandCode GOAT","CommandCode GOAT","CommandCode GOAT","CommandCode GOAT","CommandCode GOAT","CommandCode GOAT","CommandCode GOAT","CommandCode GOAT"], { aliases:['ai_providers.commandcode_goat'] }),
    Object.assign(["TypeSafe（Jev 官方）","TypeSafe (official Jev)","TypeSafe (Jev officiel)","TypeSafe (официальный Jev)","TypeSafe (Jev oficial)","TypeSafe（Jev 公式）","TypeSafe (Jev oficial)","TypeSafe (offizielles Jev)","TypeSafe (званични Jev)","TypeSafe(공식 Jev)"], { aliases:['ai_providers.typesafe'] }),
    Object.assign(["Ollama","Ollama","Ollama","Ollama","Ollama","Ollama","Ollama","Ollama","Ollama","Ollama"], { aliases:['ai_providers.local_ollama'] })
  ]);
})();
