import { useEffect, useRef, useState } from "react";
import type { Dispatch, SetStateAction } from "react";
import { createPortal } from "react-dom";
import { useLocation } from "react-router-dom";
import { ChevronDown, CircleHelp, Download, Inbox, Loader2, Plus, RefreshCw, Save, Search, Settings2, Upload, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ConfirmBubble } from "@/components/ui/confirm-bubble";
import { apiDownload, apiFetch, cn, triggerBrowserDownload } from "@/lib/utils";
import { useI18n } from "@/lib/i18n-context";
import { useSunnyGsap } from "@/lib/useSunnyGsap";

type AnyObj = Record<string, any>;
type ToastState = { type: "ok" | "fail"; text: string } | null;
type LogEntry = { id: number | string; time: string; level: string; module: string; message: string; email?: string; rawMessage?: string; detail?: AnyObj };
type RegisterStage = "register_only" | "codex_phone_bind" | "import_reverse_proxy";
const REGISTER_ONLY: RegisterStage = "register_only";
const CODEX_PHONE_BIND: RegisterStage = "codex_phone_bind";
const IMPORT_REVERSE_PROXY: RegisterStage = "import_reverse_proxy";

const sunnyStateCache = new Map<string, unknown>();
function useCachedState<T>(key: string, initial: T | (() => T)): [T, Dispatch<SetStateAction<T>>] {
  const [value, setValue] = useState<T>(() => {
    if (sunnyStateCache.has(key)) return sunnyStateCache.get(key) as T;
    return typeof initial === "function" ? (initial as () => T)() : initial;
  });
  const setCachedValue: Dispatch<SetStateAction<T>> = (next) => {
    const prev = (sunnyStateCache.has(key) ? sunnyStateCache.get(key) : value) as T;
    const resolved = typeof next === "function" ? (next as (old: T) => T)(prev) : next;
    sunnyStateCache.set(key, resolved);
    setValue(resolved);
  };
  useEffect(() => { sunnyStateCache.set(key, value); }, [key, value]);
  return [value, setCachedValue];
}

const zh = {
  workbench: "宸ヤ綔鍙?, mailbox: "閭閰嶇疆", phone: "鎺ョ爜閰嶇疆", sub2api: "鍙嶄唬閰嶇疆", proxy: "浠ｇ悊閰嶇疆", session: "Session绠＄悊",
  title: "SunnyRegister 娉ㄥ唽鏈烘帶鍒跺彴", desc: "浣跨敤鑷缓 Outlook 閭姹犳敞鍐?鐧诲綍 GPT 璐︽埛锛屽苟缁熶竴绠＄悊璐︽埛鐘舵€併€丼ession銆丷T 鍜屾棩蹇椼€?,
  register: "娉ㄥ唽鎴栫櫥褰?, refresh: "鍒锋柊", import: "瀵煎叆", save: "淇濆瓨", export: "瀵煎嚭", newGroup: "鏂板缓鍒嗙粍", move: "杩佺Щ鍒嗙粍",
  mailboxTip: "鏍煎紡锛歟mail----password----client_id----refresh_token銆傛敮鎸佹墜鍔ㄥ鍏ャ€佹枃浠跺鍏ャ€佹寚瀹氬垎缁勫拰鍗曢偖绠辫縼绉汇€?, mailboxPoolName: "鑷缓閭姹?, mailboxPoolGlobalSwitch: "浣跨敤鑷缓閭姹?, mailboxPoolSwitchTip: "鍏抽棴鍚庯紝娉ㄥ唽鏈轰笉浼氫粠鑷缓 Outlook 閭姹犲垎閰嶉偖绠便€?,
  phoneTip: "鏍煎紡锛?鎵嬫満鍙?---鎺ョ爜閾炬帴銆傛垚鍔熷悗鍐峰嵈 5 灏忔椂锛屾渶澶?3 娆°€?,
  phonePool: "鑷缓鎵嬫満鍙锋睜", phonePoolGlobalSwitch: "浣跨敤鑷缓鎵嬫満鍙锋睜", importPhones: "瀵煎叆鎵嬫満鍙?, phonePoolSwitchTip: "鍏抽棴鍚庯紝娉ㄥ唽鏈轰笉浼氫粠鑷缓鎵嬫満鍙锋睜鍒嗛厤鍙风爜锛涘悗缁彲鍒囨崲涓哄閮ㄦ帴鐮佸钩鍙般€?, phonePoolOn: "鍙敤浜庢帴鐮?, phonePoolOff: "涓嶇敤浜庢帴鐮?, phoneImportHelp: "姣忚涓€涓暱鏁堟帴鐮侊細绗竴涓瓧绗﹀繀椤绘槸 +锛屾墜鏈哄彿涓庢帴鐮侀摼鎺ヤ箣闂村繀椤讳娇鐢ㄥ洓涓腑妯嚎 ---- 杩炴帴銆?, phoneImportPlaceholder: "+12632229568----https://668.smz6.com/sms/by_key?key=xxxx", phoneImportInvalid: "鎵嬫満鍙峰鍏ユ牸寮忛敊璇?, phoneSearch: "鎼滅储鎵嬫満鍙?..", phoneNumber: "鎵嬫満鍙?, smsLink: "鎺ョ爜閾炬帴", usedCount: "宸茬敤娆℃暟", countFilter: "娆℃暟绛涢€?, allCount: "鍏ㄩ儴娆℃暟", lastUsedAt: "鏈€杩戜娇鐢ㄦ椂闂?, phoneEdit: "缂栬緫鎵嬫満鍙?, phoneStatusEnabled: "鍚敤", phoneStatusDisabled: "鍋滅敤", phoneConfirmDelete: "纭鍒犻櫎璇ユ墜鏈哄彿锛熸鎿嶄綔涓嶅彲鎾ら攢銆?, phoneConfirmBatchDelete: "纭鍒犻櫎閫変腑鐨勬墜鏈哄彿锛熸鎿嶄綔涓嶅彲鎾ら攢銆?, smsbowerProvider: "SMSBower 鎺ョ爜渚涘簲鍟?, smsbowerDesc: "褰撹嚜寤烘墜鏈哄彿姹犱笉鍙敤鎴栨棤鍙敤鍙风爜鏃讹紝娉ㄥ唽鏈轰細浣跨敤 SMSBower API 鑷姩鑾峰彇涓€娆℃€ф墜鏈哄彿銆?, smsbowerSwitch: "鍚敤 SMSBower", smsbowerReady: "SMSBower 宸查厤缃?, smsbowerApiKey: "API Key", smsbowerCountry: "榛樿鍥藉", smsbowerService: "榛樿鏈嶅姟", smsbowerMaxPrice: "鏈€澶т环鏍?, smsbowerBaseURL: "鎺ュ彛鍦板潃", smsbowerCheck: "妫€娴嬩綑棰?, smsbowerBalance: "浣欓锛歿balance}", smsbowerSaved: "SMSBower 閰嶇疆宸蹭繚瀛?, smspoolProvider: "SMSPool 鎺ョ爜渚涘簲鍟?, smspoolDesc: "SMSPool 涓存椂鍙风爜骞冲彴锛屽彲鍦ㄨ嚜寤烘墜鏈哄彿姹犲拰 SMSBower 涓嶅彲鐢ㄦ椂鑷姩璐拱涓€娆℃€ф帴鐮佸彿鐮併€?, smspoolSwitch: "鍚敤 SMSPool", smspoolReady: "SMSPool 宸查厤缃?, smspoolApiKey: "API Key", smspoolCountry: "榛樿鍥藉", smspoolService: "榛樿鏈嶅姟", refreshProviderOptions: "鑾峰彇鍒楄〃", smspoolMaxPrice: "鏈€澶т环鏍?, smspoolBaseURL: "鎺ュ彛鍦板潃", smspoolCheck: "妫€娴嬩綑棰?, smspoolBalance: "浣欓锛歿balance}", smspoolSaved: "SMSPool 閰嶇疆宸蹭繚瀛?,
  proxyTip: "绠＄悊娉ㄥ唽鏈哄彂璧锋敞鍐?鐧诲綍璇锋眰鏃朵娇鐢ㄧ殑鍑虹珯浠ｇ悊姹狅紱鎵归噺妫€娴嬩粎妫€娴嬩唬鐞嗘湇鍔¤繛閫氭€э紝涓嶈闂?ChatGPT 瀹樼綉銆?,
  proxyPool: "浠ｇ悊姹?, proxyEnabled: "鍚敤", proxyAvailable: "澶辨晥", proxySearch: "鎼滅储浠ｇ悊鍦板潃...", proxyCountry: "鍥藉", proxyAllCountry: "鍏ㄩ儴鍥藉", proxyAddress: "浠ｇ悊鍦板潃", proxyBatchCheck: "鎵归噺妫€娴?, proxyBatchDelete: "鎵归噺鍒犻櫎", proxyBatchEdit: "鎵归噺淇敼", proxyAdd: "鏂板浠ｇ悊", proxyEdit: "缂栬緫浠ｇ悊", proxyCheckDone: "浠ｇ悊妫€娴嬪畬鎴?, proxyNoData: "鏆傛棤浠ｇ悊", proxyNoDataDesc: "璇峰厛鏂板浠ｇ悊鍦板潃锛屽啀瀵瑰惎鐢ㄤ唬鐞嗚繘琛屾壒閲忔娴嬨€?, proxyStatusEnabled: "鍚敤", proxyStatusDisabled: "鍋滅敤", proxyStatusInvalid: "澶辨晥", proxyLastChecked: "涓婃妫€娴?, proxyLatency: "寤惰繜", proxyCountryPlaceholder: "渚嬪 US / HK / JP / Brazil", proxyAddressPlaceholder: "姣忚涓€涓唬鐞嗭紝渚嬪 http://user:pass@host:port 鎴?socks5://host:port", proxyConfirmDelete: "纭鍒犻櫎璇ヤ唬鐞嗭紵姝ゆ搷浣滀笉鍙挙閿€銆?, proxyConfirmBatchDelete: "纭鍒犻櫎閫変腑鐨勪唬鐞嗭紵姝ゆ搷浣滀笉鍙挙閿€銆?, proxyTrafficSwitch: "娉ㄥ唽娴侀噺浠ｇ悊", proxyTrafficOn: "浠ｇ悊寮€鍚?, proxyTrafficOff: "浠ｇ悊鍏抽棴", proxyTrafficOnHint: "娉ㄥ唽/鐧诲綍璇锋眰璧颁唬鐞嗘睜", proxyTrafficOffHint: "浣跨敤鏈嶅姟鍣ㄧ郴缁熺綉缁滃嚭鍙?, proxySwitchSaved: "浠ｇ悊鍑哄彛璁剧疆宸叉洿鏂?,
  selected: "宸查€?, globalLogs: "鍏ㄥ眬鏃ュ織", selectedLogs: "褰撳墠閭鏃ュ織", clearLogs: "娓呴櫎", latest: "鏌ヨ鏈€杩戦偖浠?, done: "鎿嶄綔瀹屾垚", failed: "鎿嶄綔澶辫触", file: "閫夋嫨鏂囦欢", status: "鐘舵€?, prev: "涓婁竴椤?, next: "涓嬩竴椤?, pageSize: "姣忛〉", pageInfo: "绗?{page} / {pages} 椤?, pageRange: "鏄剧ず {from} 鑷?{to} 鍏?{total} 鏉＄粨鏋?, noLogs: "鏆傛棤鏃ュ織", total: "鎬昏", yes: "鏄?, no: "鍚?, step: "姝ラ",
  logProxy: "浠ｇ悊", logMailbox: "閭", logPhone: "鎵嬫満", logSession: "Session", logAuth: "璁よ瘉", logSystem: "绯荤粺",
  defaultGroup: "榛樿鍒嗙粍", allGroups: "鍏ㄩ儴鍒嗙粍", mailboxGroup: "鎵€灞炲垎缁?, importMailboxes: "瀵煎叆閭", manualImport: "鎵嬪姩瀵煎叆", fileImport: "鏂囦欢瀵煎叆", dragFile: "鎷栨嫿閭鏂囦欢鍒拌繖閲岋紝鎴栫偣鍑婚€夋嫨鏂囦欢", importToGroup: "瀵煎叆鍒板垎缁?, addGroup: "鏂板缓鍒嗙粍", enterGroup: "杈撳叆鍒嗙粍鍚嶅悗鍥炶溅", validationOk: "鏍￠獙閫氳繃", validationFailed: "鏍￠獙澶辫触", mailboxList: "閭鍒楄〃", enabled: "鍚敤", updatedAt: "鏇存柊鏃堕棿", actions: "鎿嶄綔", queryMailbox: "鎼滅储閭...",
  allStatus: "鍏ㄩ儴鐘舵€?, allPlanTypes: "鍏ㄩ儴濂楅", edit: "缂栬緫", delete: "鍒犻櫎", batchDelete: "鎵归噺鍒犻櫎", batchEdit: "鎵归噺缂栬緫", confirmDeleteMailbox: "纭鍒犻櫎璇ラ偖绠辫褰曪紵姝ゆ搷浣滀笉鍙挙閿€銆?, confirmBatchDeleteMailbox: "纭鍒犻櫎閫変腑鐨勯偖绠辫褰曪紵姝ゆ搷浣滀笉鍙挙閿€銆?, queryMail: "閭欢鏌ヨ", currentMailbox: "褰撳墠閭", getMail: "鑾峰彇閭欢", mailFetchCount: "鏌ヨ鏁伴噺", mailFetchCountSuffix: "灏?, mailList: "閭欢鍒楄〃", sender: "鍙戜欢浜?, receiver: "鏀朵欢浜?, time: "鏃堕棿", subject: "涓婚", content: "閭欢鍐呭", emptyMail: "鏆傛棤閭欢", mailboxName: "閭鍚?, password: "瀵嗙爜", clientId: "client_id", refreshToken: "refresh_token", openaiAccessToken: "OpenAI Access Token", batchEditMailboxTitle: "鎵归噺缂栬緫閭", applyToSelected: "搴旂敤鍒伴€変腑鐨勯偖绠?,
  autoRegister: "鑷姩娉ㄥ唽", interruptTask: "涓柇浠诲姟", interruptTaskTip: "绔嬪嵆璇锋眰鍋滄褰撳墠娉ㄥ唽浠诲姟锛屽苟鍦ㄦ棩蹇椾腑璁板綍涓柇鎿嶄綔銆?, interruptTaskRequested: "宸茶姹備腑鏂綋鍓嶆敞鍐屼换鍔★紝Worker 灏嗗敖蹇仠姝?, interruptTaskFailed: "涓柇浠诲姟澶辫触", manualNew: "鎵嬪姩鏂板", searchAccount: "鎼滅储璐﹀彿閭...", refreshQuota: "鍒锋柊棰濆害", refreshList: "鍒锋柊鍒楄〃", refreshDone: "鍒楄〃宸插埛鏂?, refreshStatus: "鍒锋柊璐﹀彿鐘舵€?, statusChangedAt: "鐘舵€佸彉鏇存椂闂?, planType: "濂楅绫诲瀷", email: "閭", trialLink: "璇曠敤閾炬帴", registeredAt: "娉ㄥ唽鏃堕棿", operation: "鎿嶄綔", noData: "鏆傛棤鏁版嵁", noDataDesc: "褰撳墠骞冲彴娌℃湁鎵惧埌浠讳綍璐﹀彿璁板綍銆傝鍏堝埌閭閰嶇疆涓鍏ラ偖绠憋紝鐒跺悗閫夋嫨閭杩涜鑷姩娉ㄥ唽銆?, chooseMailbox: "璇烽€夋嫨閭", createTaskLog: "鍒涘缓 ChatGPT 娉ㄥ唽浠诲姟锛屾暟閲?, taskSubmitted: "娉ㄥ唽浠诲姟宸叉彁浜わ紝姝ｅ湪寮€濮嬫墽琛?, taskCreated: "鑷姩娉ㄥ唽浠诲姟宸插垱寤?, taskDone: "浠诲姟瀹屾垚", taskFailed: "浠诲姟澶辫触", taskPollRecovered: "妫€娴嬪埌涓婃娉ㄥ唽浠诲姟浠嶅湪杩涜锛屽凡鎭㈠鏃ュ織杞", taskPollLost: "浠诲姟鐘舵€佽疆璇㈣繛缁け璐ワ紝宸茶В闄ゅ墠绔敞鍐屼腑鐘舵€侊細{error}", taskPollTimeout: "浠诲姟杞瓒呰繃 30 鍒嗛挓锛屽凡瑙ｉ櫎鍓嶇娉ㄥ唽涓姸鎬侊紱璇峰埛鏂板垪琛ㄧ‘璁ゆ渶缁堢粨鏋?, importDone: "瀵煎叆瀹屾垚", exportDone: "瀵煎嚭瀹屾垚", manualNewTip: "璇峰埌閭閰嶇疆涓墜鍔ㄦ柊澧為偖绠?, autoRegisterTitle: "鑷姩娉ㄥ唽 ChatGPT", step1Title: "閫夋嫨娉ㄥ唽韬唤", step1Desc: "褰撳墠浼樺厛浣跨敤鑷缓 Outlook 閭姹犺繘琛岄偖绠遍獙璇併€?, systemMailbox: "绯荤粺閭", systemMailboxPoolDisabled: "绯荤粺閭姹犲姛鑳芥湭鍚敤锛岃鍏堝惎鐢ㄩ偖绠辨睜鍔熻兘", smsConfigDisabled: "璇峰墠寰€鎺ョ爜閰嶇疆椤甸潰鍚敤鎺ョ爜閰嶇疆", registerStageUnavailable: "璇峰厛鍚敤鑷冲皯涓€绉嶉偖绠辨敞鍐屾柟寮?, googleMailboxDisabled: "Google 閭鍔熻兘鏈惎鐢紝璇峰厛鍚敤瀵瑰簲鐨勯偖绠卞姛鑳?, microsoftMailboxDisabled: "Microsoft 閭鍔熻兘鏈惎鐢紝璇峰厛鍚敤瀵瑰簲鐨勯偖绠卞姛鑳?, systemMailboxDesc: "浣跨敤閭姹犺嚜鍔ㄦ敹鍙栭獙璇佺爜骞跺畬鎴愭敞鍐?, googleDesc: "棰勭暀韬唤锛屽悗缁帴鍏?Google 璐﹀彿", microsoftDesc: "棰勭暀韬唤锛屽悗缁帴鍏?Microsoft 璐﹀彿", step2Title: "閫夋嫨鎵ц鏂瑰紡", step2Desc: "鏀寔鍚庡彴娴忚鍣ㄨ嚜鍔ㄤ笌鍙娴忚鍣ㄨ嚜鍔紱鍚庡彴妯″紡涓嶆樉绀虹獥鍙ｏ紝鏇撮€傚悎鎵归噺鎵ц銆?, protocolMode: "鍗忚妯″紡", protocolDesc: "鍗犱綅鑳藉姏锛屾殏鏈紑鏀鹃€夋嫨", backgroundMode: "鍚庡彴娴忚鍣ㄨ嚜鍔?, backgroundDesc: "鏃犵獥鍙?Headless 鎵ц锛屼粛浣跨敤闅旂鏃犵棔娴忚鍣ㄤ笂涓嬫枃鑷姩娉ㄥ唽", visibleMode: "鍙娴忚鍣ㄨ嚜鍔?, visibleDesc: "浼氭墦寮€娴忚鍣ㄧ獥鍙ｏ紝閫傚悎鎺掓煡浜烘満楠岃瘉鎴栭〉闈㈠紓甯?, registerCount: "娉ㄥ唽鏁伴噺", concurrency: "骞跺彂鏁?, identityLabel: "娉ㄥ唽韬唤", modeLabel: "鎵ц鏂瑰紡", registerAccounts: "娉ㄥ唽璐﹀彿", verifyStrategy: "楠岃瘉绛栫暐锛氫娇鐢?Outlook IMAP/XOAUTH2 鑷姩璇诲彇楠岃瘉鐮?, step3Title: "閫夋嫨娉ㄥ唽闃舵", step3Desc: "鎺у埗鏈浠诲姟鎵ц鍒板摢涓樁娈碉紝榛樿浠呭畬鎴?ChatGPT 娉ㄥ唽/鐧诲綍涓?Session 瀛樺偍銆?, registerOnly: "浠呮敞鍐?ChatGPT", registerOnlyDesc: "娉ㄥ唽鎴栫櫥褰曟垚鍔熷悗锛屽彧璇诲彇骞朵繚瀛?ChatGPT Session 淇℃伅", codexPhoneBind: "Codex鎺ョ爜缁戝畾", codexPhoneBindDesc: "娉ㄥ唽/鐧诲綍鍚庣户缁娇鐢ㄦ帴鐮侀厤缃畬鎴愭墜鏈洪獙璇佸苟鑾峰彇 Refresh Token", importReverseProxy: "瀵煎叆鍙嶄唬骞冲彴", importReverseProxyDesc: "瀹屾垚璐﹀彿 Session/RT 鍚庡鍏ュ凡閰嶇疆鐨?sub2api 鍙嶄唬骞冲彴", stageLabel: "娉ㄥ唽闃舵", startAutoRegister: "寮€濮嬭嚜鍔ㄦ敞鍐?, cancel: "鍙栨秷", noMailbox: "鏆傛棤閭", noMailboxDesc: "璇风偣鍑诲彸涓婅鈥滃鍏ラ偖绠扁€濇坊鍔犺嚜寤?Outlook 閭姹犮€?, inbox: "鏀朵欢绠?, fillOrChooseMailboxFile: "璇峰厛濉啓鎴栭€夋嫨閭鏂囦欢",
  sub2apiDesc: "鐢ㄤ簬鈥滃鍏ュ弽浠ｅ钩鍙扳€濋樁娈点€傚～鍐?sub2api 鍦板潃涓庣鐞嗗憳 Key 鍚庯紝娉ㄥ唽浠诲姟鍙皢宸茶幏鍙?Session/RT 鐨?GPT 璐﹀彿瀵煎叆骞冲彴銆?, baseURL: "Base URL", adminToken: "Admin Token", accountNamePrefix: "璐﹀彿鍚嶅墠缂€", targetGroup: "鐩爣鍒嗙粍", targetGroupPlaceholder: "璇烽€夋嫨鐩爣鍒嗙粍", noGroupsFetch: "鏆傛棤鍒嗙粍锛岃鐐瑰嚮鍙充晶鈥滆幏鍙栤€?, fetch: "鑾峰彇", priority: "浼樺厛绾?, check: "妫€娴?, configUnchanged: "閰嶇疆鏈洿鏀?, fillURLToken: "璇峰厛濉啓 Base URL 鍜?Admin Token", fetchedGroups: "宸茶幏鍙?{count} 涓洰鏍囧垎缁?, fillURLTokenShort: "璇峰厛濉啓 URL 鍜?Token", checking: "妫€娴嬩腑...", checkPassedGroups: "妫€娴嬮€氳繃锛屽彂鐜?{count} 涓垎缁?, checkFailed: "妫€娴嬪け璐ワ細{error}", lineFormatPhone: "+鎵嬫満鍙?---https://鎺ョ爜閾炬帴", sessionJSON: "Auth Session", accessToken: "Access Token", mailboxAccountExport: "閭璐︽埛", exportFormat: "瀵煎嚭鍐呭", selectExportRows: "璇烽€夋嫨闇€瑕佸鍑虹殑璐﹀彿", tokenPreview: "Token棰勮", sessionRefreshToken: "Refresh Token", updated: "鏇存柊鏃堕棿",
  linkedMailboxConfig: "鑱斿姩閭閰嶇疆", linkedPhoneConfig: "鑱斿姩鎺ョ爜閰嶇疆", linkedReverseConfig: "鑱斿姩鍙嶄唬閰嶇疆", resourceReady: "鍙敤", resourceMissing: "涓嶅彲鐢?, usablePhones: "鍙敤鎵嬫満鍙?{count} 涓?, existingRTReady: "鎵€閫夎处鍙峰凡鏈?RT锛屾棤闇€鎺ョ爜", sub2apiReady: "sub2api 宸查厤缃?, sub2apiMissing: "sub2api 鏈畬鏁撮厤缃?, stageDisabledTip: "璇ラ樁娈典緷璧栫殑閰嶇疆鏆備笉鍙敤锛岃鍏堝畬鎴愬搴旇彍鍗曢厤缃€?,
  statusLabels: { "鏈敞鍐?: "鏈敞鍐?, "宸叉敞鍐?: "宸叉敞鍐?, "registered": "宸叉敞鍐?, "宸叉帴鐮?: "宸叉帴鐮?, "PLUS璇曠敤涓?: "PLUS璇曠敤涓?, "宸插皝绂?: "宸插皝绂?, "闇€浜岄獙": "闇€浜岄獙", "娉ㄥ唽涓?: "娉ㄥ唽涓?, "鐧诲綍鍒锋柊": "鐧诲綍鍒锋柊", "澶辫触": "澶辫触", "failed": "澶辫触", "绂佺敤": "绂佺敤" },
};
const en = {
  workbench: "Workbench", mailbox: "Mailbox", phone: "SMS", sub2api: "Reverse Proxy", proxy: "Proxy", session: "Sessions",
  title: "SunnyRegister Console", desc: "Register/login GPT accounts with a self-managed Outlook mailbox pool, then manage account status, sessions, RTs and logs.",
  register: "Register / Login", refresh: "Refresh", import: "Import", save: "Save", export: "Export", newGroup: "New Group", move: "Move Group",
  mailboxTip: "Format: email----password----client_id----refresh_token. Supports manual import, file import, group import and moving single mailboxes.", mailboxPoolName: "Self-managed Mailbox Pool", mailboxPoolGlobalSwitch: "Use Self-managed Mailbox Pool", mailboxPoolSwitchTip: "When disabled, SunnyRegister will not allocate mailboxes from the self-managed Outlook mailbox pool.",
  phoneTip: "Format: +phone----SMS URL. Cooldown 5 hours after success, max 3 successes.",
  phonePool: "Self-managed Phone Pool", phonePoolGlobalSwitch: "Use Self-managed Phone Pool", importPhones: "Import Phones", phonePoolSwitchTip: "When disabled, SunnyRegister will not allocate numbers from this phone pool. You can switch to external SMS providers later.", phonePoolOn: "Usable for SMS", phonePoolOff: "Not used for SMS", phoneImportHelp: "One long-lived SMS record per line. The first character must be +, and the phone number and SMS URL must be separated with exactly four hyphens: ----.", phoneImportPlaceholder: "+12632229568----https://668.smz6.com/sms/by_key?key=xxxx", phoneImportInvalid: "Invalid phone import format", phoneSearch: "Search phone number...", phoneNumber: "Phone Number", smsLink: "SMS Link", usedCount: "Used Count", countFilter: "Count", allCount: "All Counts", lastUsedAt: "Last Used", phoneEdit: "Edit Phone", phoneStatusEnabled: "Enabled", phoneStatusDisabled: "Disabled", phoneConfirmDelete: "Delete this phone number? This cannot be undone.", phoneConfirmBatchDelete: "Delete selected phone numbers? This cannot be undone.", smsbowerProvider: "SMSBower Provider", smsbowerDesc: "When the self-managed phone pool is unavailable or empty, SunnyRegister can use SMSBower API to rent a one-time number automatically.", smsbowerSwitch: "Enable SMSBower", smsbowerReady: "SMSBower configured", smsbowerApiKey: "API Key", smsbowerCountry: "Default Country", smsbowerService: "Default Service", smsbowerMaxPrice: "Max Price", smsbowerBaseURL: "API URL", smsbowerCheck: "Check Balance", smsbowerBalance: "Balance: {balance}", smsbowerSaved: "SMSBower config saved", smspoolProvider: "SMSPool Provider", smspoolDesc: "SMSPool is a temporary-number provider used when the self-managed phone pool and SMSBower are unavailable.", smspoolSwitch: "Enable SMSPool", smspoolReady: "SMSPool configured", smspoolApiKey: "API Key", smspoolCountry: "Default Country", smspoolService: "Default Service", refreshProviderOptions: "Fetch options", smspoolMaxPrice: "Max Price", smspoolBaseURL: "API URL", smspoolCheck: "Check Balance", smspoolBalance: "Balance: {balance}", smspoolSaved: "SMSPool config saved",
  proxyTip: "Manage the outbound proxy pool for register/login requests. Batch check only tests proxy server connectivity and does not access chatgpt.com.",
  proxyPool: "Proxy Pool", proxyEnabled: "Enabled", proxyAvailable: "Invalid", proxySearch: "Search proxy address...", proxyCountry: "Country", proxyAllCountry: "All Countries", proxyAddress: "Proxy Address", proxyBatchCheck: "Batch Check", proxyBatchDelete: "Batch Delete", proxyBatchEdit: "Batch Edit", proxyAdd: "Add Proxy", proxyEdit: "Edit Proxy", proxyCheckDone: "Proxy check completed", proxyNoData: "No Proxies", proxyNoDataDesc: "Add proxy addresses first, then batch-check enabled proxies.", proxyStatusEnabled: "Enabled", proxyStatusDisabled: "Disabled", proxyStatusInvalid: "Invalid", proxyLastChecked: "Last Checked", proxyLatency: "Latency", proxyCountryPlaceholder: "e.g. US / HK / JP / Brazil", proxyAddressPlaceholder: "One proxy per line, e.g. http://user:pass@host:port or socks5://host:port", proxyConfirmDelete: "Delete this proxy? This cannot be undone.", proxyConfirmBatchDelete: "Delete selected proxies? This cannot be undone.", proxyTrafficSwitch: "Register Traffic Proxy", proxyTrafficOn: "Proxy On", proxyTrafficOff: "Proxy Off", proxyTrafficOnHint: "Register/login requests use proxy pool", proxyTrafficOffHint: "Use server/system network", proxySwitchSaved: "Proxy outlet setting updated",
  selected: "Selected", globalLogs: "Global Logs", selectedLogs: "Current Mailbox Logs", clearLogs: "Clear", latest: "Latest Mail", done: "Done", failed: "Failed", file: "Choose File", status: "Status", prev: "Prev", next: "Next", pageSize: "Per page", pageInfo: "Page {page} / {pages}", pageRange: "Showing {from} to {to} of {total} results", noLogs: "No logs", total: "Total", yes: "Yes", no: "No", step: "STEP",
  logProxy: "Proxy", logMailbox: "Mailbox", logPhone: "Phone", logSession: "Session", logAuth: "Auth", logSystem: "System",
  defaultGroup: "Default Group", allGroups: "All Groups", mailboxGroup: "Group", importMailboxes: "Import Mailboxes", manualImport: "Manual", fileImport: "File", dragFile: "Drag mailbox file here, or click to choose a file", importToGroup: "Import to group", addGroup: "New Group", enterGroup: "Type group name and press Enter", validationOk: "Validation passed", validationFailed: "Validation failed", mailboxList: "Mailbox List", enabled: "Enabled", updatedAt: "Updated", actions: "Actions", queryMailbox: "Search mailbox...",
  allStatus: "All Status", allPlanTypes: "All Plans", edit: "Edit", delete: "Delete", batchDelete: "Batch Delete", batchEdit: "Batch Edit", confirmDeleteMailbox: "Delete this mailbox record? This cannot be undone.", confirmBatchDeleteMailbox: "Delete the selected mailbox records? This cannot be undone.", queryMail: "Mail Query", currentMailbox: "Current Mailbox", getMail: "Get Mail", mailFetchCount: "Count", mailFetchCountSuffix: "mails", mailList: "Mail List", sender: "Sender", receiver: "Receiver", time: "Time", subject: "Subject", content: "Content", emptyMail: "No mails", mailboxName: "Mailbox", password: "Password", clientId: "client_id", refreshToken: "refresh_token", openaiAccessToken: "OpenAI Access Token", batchEditMailboxTitle: "Batch Edit Mailboxes", applyToSelected: "Apply to selected mailboxes",
  autoRegister: "Auto Register", interruptTask: "Interrupt Task", interruptTaskTip: "Request the current registration task to stop immediately and write a cancellation log.", interruptTaskRequested: "Interrupt requested; the Worker will stop as soon as possible", interruptTaskFailed: "Failed to interrupt task", manualNew: "Manual Add", searchAccount: "Search account email...", refreshQuota: "Refresh Quota", refreshList: "Refresh List", refreshDone: "List refreshed", refreshStatus: "Refresh Account Status", statusChangedAt: "Status Changed At", planType: "Plan Type", email: "Email", trialLink: "Trial Link", registeredAt: "Registered At", operation: "Action", noData: "No Data", noDataDesc: "No mailbox records were found. Import mailboxes in Mailbox settings, then select mailboxes to start auto registration.", chooseMailbox: "Please select mailboxes", createTaskLog: "Created ChatGPT register task, count", taskSubmitted: "Registration task submitted and starting", taskCreated: "Auto register task created", taskDone: "Task completed", taskFailed: "Task failed", taskPollRecovered: "Detected an unfinished registration task and resumed log polling", taskPollLost: "Task status polling failed repeatedly; frontend registering state has been released: {error}", taskPollTimeout: "Task polling exceeded 30 minutes; frontend registering state has been released. Refresh the list to confirm the final result.", importDone: "Import completed", exportDone: "Export completed", manualNewTip: "Please add mailboxes manually in Mailbox settings", autoRegisterTitle: "Auto Register ChatGPT", step1Title: "Choose Identity", step1Desc: "The self-managed Outlook mailbox pool is used first for email verification.", systemMailbox: "System Mailbox", systemMailboxPoolDisabled: "System mailbox pool is not enabled. Please enable the mailbox pool first.", smsConfigDisabled: "Please enable SMS settings on the SMS configuration page first.", registerStageUnavailable: "Please enable at least one mailbox registration method first.", googleMailboxDisabled: "Google mailbox is not enabled. Please enable the corresponding mailbox feature first.", microsoftMailboxDisabled: "Microsoft mailbox is not enabled. Please enable the corresponding mailbox feature first.", systemMailboxDesc: "Use mailbox pool to receive verification codes and complete registration", googleDesc: "Reserved identity; Google account integration will be added later", microsoftDesc: "Reserved identity; Microsoft account integration will be added later", step2Title: "Choose Execution Mode", step2Desc: "Background browser and visible browser automation are supported. Background mode runs without a window and is better for batches.", protocolMode: "Protocol Mode", protocolDesc: "Reserved; not selectable yet", backgroundMode: "Background Browser", backgroundDesc: "Run headless without a visible window while still using an isolated incognito browser context", visibleMode: "Visible Browser", visibleDesc: "Open a browser window for easier challenge or page issue troubleshooting", registerCount: "Register Count", concurrency: "Concurrency", identityLabel: "Identity", modeLabel: "Execution Mode", registerAccounts: "Accounts", verifyStrategy: "Verification: read code automatically with Outlook IMAP/XOAUTH2", step3Title: "Choose Registration Stage", step3Desc: "Control how far this task should run. Default only completes ChatGPT registration/login and Session storage.", registerOnly: "Register ChatGPT Only", registerOnlyDesc: "After register/login, only read and save ChatGPT Session info", codexPhoneBind: "Codex Phone Binding", codexPhoneBindDesc: "Continue phone verification with SMS settings and acquire Refresh Token", importReverseProxy: "Import Reverse Proxy", importReverseProxyDesc: "Import the account into configured sub2api after Session/RT is ready", stageLabel: "Stage", startAutoRegister: "Start Auto Register", cancel: "Cancel", noMailbox: "No Mailboxes", noMailboxDesc: "Click 鈥淚mport Mailboxes鈥?in the upper-right corner to add your Outlook mailbox pool.", inbox: "Inbox", fillOrChooseMailboxFile: "Please fill in or choose a mailbox file",
  sub2apiDesc: "Used by the 鈥淚mport Reverse Proxy鈥?stage. After Base URL and Admin Key are configured, registration tasks can import GPT accounts with Session/RT into the platform.", baseURL: "Base URL", adminToken: "Admin Token", accountNamePrefix: "Account Name Prefix", targetGroup: "Target Group", targetGroupPlaceholder: "Select target groups", noGroupsFetch: "No groups yet. Click 鈥淔etch鈥?on the right.", fetch: "Fetch", priority: "Priority", check: "Check", configUnchanged: "Configuration unchanged", fillURLToken: "Please fill in Base URL and Admin Token first", fetchedGroups: "Fetched {count} target groups", fillURLTokenShort: "Please fill in URL and Token first", checking: "Checking...", checkPassedGroups: "Check passed, found {count} groups", checkFailed: "Check failed: {error}", lineFormatPhone: "+phone----https://sms-url", sessionJSON: "Auth Session", accessToken: "Access Token", mailboxAccountExport: "Mailbox Account", exportFormat: "Export Content", selectExportRows: "Please select accounts to export", tokenPreview: "Token Preview", sessionRefreshToken: "Refresh Token", updated: "Updated",
  linkedMailboxConfig: "Uses Mailbox config", linkedPhoneConfig: "Uses SMS config", linkedReverseConfig: "Uses Reverse Proxy config", resourceReady: "Ready", resourceMissing: "Unavailable", usablePhones: "{count} usable phones", existingRTReady: "Selected accounts already have RT; SMS is not required", sub2apiReady: "sub2api configured", sub2apiMissing: "sub2api incomplete", stageDisabledTip: "The configuration required by this stage is unavailable. Complete the linked menu first.",
  statusLabels: { "鏈敞鍐?: "Unregistered", "宸叉敞鍐?: "Registered", "registered": "Registered", "宸叉帴鐮?: "Phone Bound", "PLUS璇曠敤涓?: "PLUS Trial", "宸插皝绂?: "Banned", "闇€浜岄獙": "Needs 2FA", "娉ㄥ唽涓?: "Registering", "鐧诲綍鍒锋柊": "Refreshing Login", "澶辫触": "Failed", "failed": "Failed", "绂佺敤": "Disabled" },
};

const MAILBOX_STATUSES = ["鏈敞鍐?, "宸叉敞鍐?, "宸叉帴鐮?, "PLUS璇曠敤涓?, "宸插皝绂?, "闇€浜岄獙"];
const PLAN_TYPE_OPTIONS = ["free", "plus", "k12", "team", "pro"];
function template(text: string, values: Record<string, string | number>) {
  return text.replace(/\{(\w+)\}/g, (_, key) => String(values[key] ?? ""));
}

function Tip({ text }: { text: string }) { return <span title={text} className="inline-flex"><CircleHelp className="tip-icon h-4 w-4" /></span>; }
function Label({ children, tip }: { children: React.ReactNode; tip?: string }) { return <div className="form-label mb-2"><span className="inline-flex items-center gap-1.5">{children}{tip && <Tip text={tip} />}</span></div>; }
function Input(props: React.InputHTMLAttributes<HTMLInputElement>) { return <input {...props} className={cn("control-surface h-11", props.className)} />; }
function Textarea(props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) { return <textarea {...props} className={cn("control-surface min-h-28", props.className)} />; }
function SelectBox({ value, onChange, options, className }: { value: string | number; onChange: (v: string | number) => void; options: { value: string | number; label: React.ReactNode }[]; className?: string }) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [menuRect, setMenuRect] = useState<{ left: number; top: number; width: number; maxHeight: number } | null>(null);
  const active = options.find((x) => String(x.value) === String(value)) || options[0];
  const updateRect = () => {
    const rect = wrapRef.current?.getBoundingClientRect();
    if (rect) {
      const desiredHeight = Math.min(320, options.length * 44 + 12);
      const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 800;
      const spaceBelow = viewportHeight - rect.bottom - 14;
      const spaceAbove = rect.top - 14;
      const openUp = spaceBelow < desiredHeight && spaceAbove > spaceBelow;
      const maxHeight = Math.max(120, openUp ? spaceAbove - 8 : spaceBelow - 8);
      setMenuRect({
        left: rect.left,
        top: openUp ? Math.max(12, rect.top - Math.min(desiredHeight, maxHeight) - 8) : rect.bottom + 8,
        width: rect.width,
        maxHeight,
      });
    }
  };
  useEffect(() => {
    if (!open) return;
    updateRect();
    const onMove = () => updateRect();
    window.addEventListener("scroll", onMove, true);
    window.addEventListener("resize", onMove);
    return () => {
      window.removeEventListener("scroll", onMove, true);
      window.removeEventListener("resize", onMove);
    };
  }, [open]);
  const menu = open && menuRect ? createPortal(<div className={cn("sr-custom-select-menu sr-custom-select-menu-portal", className?.includes("sr-page-size-select") && "sr-page-size-select-menu")} style={{ position: "fixed", left: menuRect.left, top: menuRect.top, width: menuRect.width, maxHeight: menuRect.maxHeight, overflowY: "auto", right: "auto", zIndex: 5000 }}>
      {options.map((opt) => <button type="button" key={String(opt.value)} className={cn("sr-custom-select-option", String(opt.value) === String(value) && "active")} onMouseDown={(e)=>e.preventDefault()} onClick={() => { onChange(opt.value); setOpen(false); }}>{opt.label}</button>)}
    </div>, document.body) : null;
  return <div ref={wrapRef} className={cn("sr-custom-select", className)} tabIndex={0} onBlur={() => window.setTimeout(() => setOpen(false), 120)}>
    <button type="button" className={cn("sr-custom-select-trigger", open && "open")} onClick={() => { updateRect(); setOpen((v) => !v); }}>
      <span>{active?.label}</span><ChevronDown className={cn("h-4 w-4 transition-transform", open && "rotate-180")} />
    </button>
    {menu}
  </div>;
}
function Toast({ toast, clear }: { toast: ToastState; clear: () => void }) {
  const [hovering, setHovering] = useState(false);
  useEffect(() => {
    if (!toast || hovering) return;
    const timer = window.setTimeout(clear, 2600);
    return () => window.clearTimeout(timer);
  }, [toast, hovering, clear]);
  useEffect(() => {
    if (!toast) setHovering(false);
  }, [toast]);
  return toast ? <div className={cn("sr-toast", toast.type === "ok" ? "ok" : "fail")} onMouseEnter={() => setHovering(true)} onMouseLeave={() => setHovering(false)}>
    <span>{toast.text}</span><button onClick={clear}><X className="h-4 w-4" /></button>
  </div> : null;
}
function formatDateTime(value: any) {
  if (!value) return "-";
  const raw = String(value);
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) return raw.replace("T", " ").replace(/\.\d+Z?$/, "").slice(0, 19) || "-";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}
type SortOrder = "asc" | "desc";
function nextSortOrder(v: SortOrder): SortOrder { return v === "asc" ? "desc" : "asc"; }
function SortTimeHeader({ label, order, onToggle }: { label: string; order: SortOrder; onToggle: () => void }) {
  return <button type="button" className="sr-sort-th" onClick={onToggle} title={order === "asc" ? "ASC" : "DESC"}><span>{label}</span><span className="sr-sort-icon">{order === "asc" ? "鈫? : "鈫?}</span></button>;
}
const PAGE_SIZE_OPTIONS = [10, 20, 50, 100];
function pageCount(total: number, pageSize: number) {
  return Math.max(1, Math.ceil(Math.max(0, Number(total || 0)) / Math.max(1, Number(pageSize || 10))));
}
function paginationTokens(page: number, pages: number): Array<number | "..."> {
  if (pages <= 7) return Array.from({ length: pages }, (_, i) => i + 1);
  const out: Array<number | "..."> = [1];
  const start = Math.max(2, page - 1);
  const end = Math.min(pages - 1, page + 1);
  if (start > 2) out.push("...");
  for (let n = start; n <= end; n++) out.push(n);
  if (end < pages - 1) out.push("...");
  out.push(pages);
  return out;
}
function PaginationBar({ t, total, page, pageSize, setPage, setPageSize }: { t: typeof zh; total: number; page: number; pageSize: number; setPage: (v: number) => void; setPageSize: (v: number) => void }) {
  const pages = pageCount(total, pageSize);
  const safePage = Math.min(Math.max(1, page), pages);
  const from = total <= 0 ? 0 : (safePage - 1) * pageSize + 1;
  const to = Math.min(total, safePage * pageSize);
  const tokens = paginationTokens(safePage, pages);
  return <div className="sr-pagination">
    <div className="sr-pagination-left">
      <span className="sr-pagination-range">{template(t.pageRange, { from, to, total })}</span>
      <span className="sr-page-size-label">{t.pageSize}:</span>
      <SelectBox className="sr-page-size-select" value={pageSize} onChange={(v)=>{ setPageSize(Number(v)); setPage(1); }} options={PAGE_SIZE_OPTIONS.map((n)=>({value:n,label:String(n)}))} />
    </div>
    <div className="sr-pagination-actions" aria-label="pagination">
      <button type="button" className="sr-page-nav" disabled={safePage<=1 || total <= 0} onClick={()=>setPage(safePage-1)} title={t.prev}>鈥?/button>
      {tokens.map((token, idx) => token === "..."
        ? <span key={`ellipsis-${idx}`} className="sr-page-ellipsis">鈥?/span>
        : <button key={token} type="button" className={cn("sr-page-number", token === safePage && "active")} onClick={()=>setPage(token)}>{token}</button>
      )}
      <button type="button" className="sr-page-nav" disabled={safePage>=pages || total <= 0} onClick={()=>setPage(safePage+1)} title={t.next}>鈥?/button>
    </div>
  </div>;
}
function logModule(message: string) {
  const text = String(message || "").replace(/^\[[^\]\s]+@[^\]\s]+\]\s*/, "").trim();
  const explicit = text.match(/^\[([^\]]+)\]/);
  if (explicit) return explicit[1];
  const lower = text.toLowerCase();
  if (/proxy|浠ｇ悊|鍑哄彛|ipinfo/.test(lower)) return "浠ｇ悊";
  if (/imap|閭|閭欢|楠岃瘉鐮亅otp/.test(lower)) return "閭";
  if (/鎵嬫満|鐢佃瘽|sms|phone/.test(lower)) return "鎵嬫満";
  if (/session|access token|accesstoken|rt/.test(lower)) return "Session";
  if (/娉ㄥ唽|鐧诲綍|璁よ瘉|oauth|auth|chatgpt|openai/.test(lower)) return "璁よ瘉";
  return "绯荤粺";
}
function logMessage(message: string) {
  return String(message || "").replace(/^\[[^\]\s]+@[^\]\s]+\]\s*/, "").replace(/^\[[^\]]+\]\s*/, "").trim();
}
function logFromEvent(event: AnyObj): LogEntry {
  const message = String(event.message || event.line || "");
  const detail = event.detail || {};
  return {
    id: event.id || `${Date.now()}-${Math.random()}`,
    time: formatDateTime(detail.local_created_at || event.created_at || new Date()).slice(11, 19),
    level: String(event.level || "info"),
    module: logModule(message),
    message: logMessage(message),
    email: String(detail.email || event.email || ""),
    rawMessage: message,
    detail,
  };
}
function localLog(message: string, level = "info"): LogEntry {
  return { id: `${Date.now()}-${Math.random()}`, time: new Date().toLocaleTimeString("zh-CN", { hour12: false }), level, module: logModule(message), message: logMessage(message), rawMessage: message, detail: {} };
}
function batchSeparatorLog(label: string): LogEntry {
  return { id: `sep-${Date.now()}-${Math.random()}`, time: new Date().toLocaleTimeString("zh-CN", { hour12: false }), level: "separator", module: "绯荤粺", message: label, rawMessage: label, detail: { separator: true } };
}

export default function SunnyRegister() {
  const { language } = useI18n();
  const t = language === "en-US" ? en : zh;
  const location = useLocation();
  const rootRef = useRef<HTMLDivElement | null>(null);
  const page = location.pathname.includes("mailbox") ? "mailbox" : location.pathname.includes("phone") ? "phone" : location.pathname.includes("sub2api") ? "sub2api" : location.pathname.includes("proxy") ? "proxy" : location.pathname.includes("session") ? "session" : "workbench";
  useSunnyGsap(rootRef, page);
  const [toast, setToast] = useState<ToastState>(null);
  const notify = (type: "ok" | "fail", text: string) => { setToast({ type, text }); };
  return <div ref={rootRef} className="sunny-page space-y-6"><Toast toast={toast} clear={() => setToast(null)} />{page === "workbench" && <Hero t={t} />}{page === "workbench" && <Workbench t={t} notify={notify} />}{page === "mailbox" && <MailboxConfig t={t} notify={notify} />}{page === "phone" && <PhoneConfig t={t} notify={notify} />}{page === "sub2api" && <Sub2APIConfig t={t} notify={notify} />}{page === "proxy" && <ProxyConfigPage t={t} notify={notify} />}{page === "session" && <SessionManager t={t} notify={notify} />}</div>;
}

function Hero({ t }: { t: typeof zh }) { return <section className="hero-card rounded-[34px] border border-[var(--border)] p-6 md:p-8"><Badge className="rounded-full px-3 py-1">SunnyRegister</Badge><h1 className="mt-4 text-4xl font-black tracking-[-0.05em] md:text-5xl">{t.title}</h1><p className="mt-3 max-w-4xl leading-7 text-[var(--text-secondary)]">{t.desc}</p></section>; }

function Workbench({ t, notify }: { t: typeof zh; notify: (type: "ok" | "fail", text: string) => void }) {
  const [accounts, setAccounts] = useCachedState<AnyObj[]>("workbench.accounts", []);
  const [mailboxes, setMailboxes] = useCachedState<AnyObj[]>("workbench.mailboxes", []);
  const [groups, setGroups] = useCachedState<AnyObj[]>("workbench.groups", []);
  const [selected, setSelected] = useCachedState<number[]>("workbench.selected", []);
  const [query, setQuery] = useCachedState("workbench.query", "");
  const [status, setStatus] = useCachedState("workbench.status", "");
  const [planFilter, setPlanFilter] = useCachedState("workbench.planFilter", "");
  const [groupFilter, setGroupFilter] = useCachedState("workbench.groupFilter", 0);
  const [pageNo, setPageNo] = useCachedState("workbench.page", 1);
  const [pageSize, setPageSize] = useCachedState("workbench.pageSize", 10);
  const [total, setTotal] = useCachedState("workbench.total", 0);
  const [timeSort, setTimeSort] = useCachedState<SortOrder>("workbench.timeSort", "desc");
  const [busy, setBusy] = useCachedState("workbench.busy", false);
  const [activeTaskId, setActiveTaskId] = useCachedState("workbench.activeTaskId", "");
  const [activeTaskMailboxIds, setActiveTaskMailboxIds] = useCachedState<number[]>("workbench.activeTaskMailboxIds", []);
  const [autoOpen, setAutoOpen] = useCachedState("workbench.autoOpen", false);
  const [modalConcurrency, setModalConcurrency] = useCachedState("workbench.concurrency", 1);
  const [identity, setIdentity] = useCachedState<"system" | "google" | "microsoft">("workbench.identity", "system");
  const [mode, setMode] = useCachedState<"protocol" | "background" | "visible">("workbench.mode", "visible");
  const [stage, setStage] = useCachedState<RegisterStage>("workbench.stage", "register_only");
  const [globalLogs, setGlobalLogs] = useCachedState<LogEntry[]>("workbench.globalLogs", []);
  const [selectedLogs, setSelectedLogs] = useCachedState<LogEntry[]>("workbench.selectedLogs", []);
  const [, setCurrentLogEmail] = useCachedState("workbench.currentLogEmail", "");
  const pollingTaskIdsRef = useRef<Set<string>>(new Set());
  const resumedTaskIdsRef = useRef<Set<string>>(new Set());
  const ignoredTaskIdsRef = useRef<Set<string>>(new Set());
  const load = async () => {
    const params = new URLSearchParams({ page: String(pageNo), page_size: String(pageSize), enabled: "true", sort_by: "updated_at", sort_order: timeSort });
    if (query.trim()) params.set("q", query.trim());
    if (groupFilter) params.set("group_id", String(groupFilter));
    if (status) params.set("status", status);
    if (planFilter) params.set("plan_type", planFilter);
    const [a, m, g] = await Promise.all([apiFetch("/sunny/workbench/accounts"), apiFetch(`/sunny/mailboxes?${params.toString()}`), apiFetch("/sunny/mailbox-groups")]);
    setAccounts(a.items || []);
    setMailboxes(m.items || []);
    setTotal(Number(m.total || 0));
    setGroups(g.items || []);
  };
  const refreshList = async () => {
    try {
      await load();
      notify("ok", t.refreshDone);
    } catch (e: any) {
      notify("fail", e.message || String(e));
    }
  };
  useEffect(() => { void load(); }, [pageNo, pageSize, query, status, planFilter, groupFilter, timeSort]);
  const rows: AnyObj[] = mailboxes
    .map((m: AnyObj) => ({ ...m, account: accounts.find((a: AnyObj) => a.email === m.email) || {} }) as AnyObj);
  const safePageNo = Math.min(Math.max(1, pageNo), pageCount(total, pageSize));
  const pagedRows = rows;
  useEffect(()=>{setPageNo(1)},[query, status, planFilter, groupFilter, pageSize, timeSort]);
  useEffect(()=>{if (pageNo !== safePageNo) setPageNo(safePageNo)},[pageNo, safePageNo]);
  async function createRegisterTask(directIds?: number[]) {
    const ids = directIds?.length ? directIds : visibleSelected;
    if (!ids.length) { notify("fail", t.chooseMailbox); return; }
    setBusy(true);
    const sep = batchSeparatorLog(`========= SunnyRegister ${t.autoRegister} 路 ${formatDateTime(new Date())} =========`);
    setGlobalLogs((old) => [localLog(`${t.createTaskLog} ${ids.length}`), sep, ...old]);
    setSelectedLogs((old) => [sep, ...old]);
    try {
      const res = await apiFetch("/sunny/tasks/register", { method: "POST", body: JSON.stringify({ mailbox_ids: ids, count: ids.length, concurrency: Math.max(1, Math.min(Number(modalConcurrency) || 1, ids.length)), identity, execution_mode: mode, registration_stage: stage }) });
      notify("ok", t.taskSubmitted);
      setGlobalLogs((old) => [localLog(t.taskSubmitted), ...old].slice(0, 160));
      setAutoOpen(false);
      const taskId = String(res.id || res.task_id || "");
      setActiveTaskId(taskId);
      setActiveTaskMailboxIds(ids);
      void poll(taskId, ids);
    } catch (e: any) {
      notify("fail", e.message || String(e));
      setBusy(false);
      setActiveTaskId("");
      setActiveTaskMailboxIds([]);
    }
  }
  async function cancelActiveTask() {
    const taskId = String(activeTaskId || "");
    if (!taskId) return;
    const msg = t.interruptTaskRequested;
    setGlobalLogs((old) => [localLog(msg, "warning"), ...old].slice(0, 200));
    setSelectedLogs((old) => [localLog(msg, "warning"), ...old].slice(0, 200));
    try {
      await apiFetch(`/tasks/${taskId}/cancel`, { method: "POST" });
      ignoredTaskIdsRef.current.add(taskId);
      notify("ok", msg);
      setBusy(false);
      setActiveTaskId("");
      setActiveTaskMailboxIds([]);
      void load();
    } catch (e: any) {
      notify("fail", `${t.interruptTaskFailed}: ${e?.message || String(e)}`);
    }
  }
  async function poll(id: string, ids: number[]) {
    const taskId = String(id || "");
    if (!taskId) {
      setBusy(false);
      setActiveTaskId("");
      setActiveTaskMailboxIds([]);
      return;
    }
    if (pollingTaskIdsRef.current.has(taskId)) return;
    pollingTaskIdsRef.current.add(taskId);
    let last = 0;
    const emails = mailboxes.filter((m) => ids.includes(m.id)).map((m) => String(m.email || "").toLowerCase());
    let activeLogEmail = "";
    let failures = 0;
    try {
      for (let i = 0; i < 1800; i++) {
        if (ignoredTaskIdsRef.current.has(taskId)) return;
        try {
          const [task, ev] = await Promise.all([apiFetch(`/tasks/${taskId}`), apiFetch(`/tasks/${taskId}/events?since=${last}`)]);
          failures = 0;
          const items = ev.items || [];
          if (items.length) {
            last = Math.max(...items.map((x: AnyObj) => x.id));
            const entries: LogEntry[] = items.map((item: AnyObj) => logFromEvent(item));
            setGlobalLogs((old) => [...entries, ...old].slice(0, 200));
            const scoped = entries.filter((x) => x.email && (!emails.length || emails.includes(x.email.toLowerCase())));
            if (scoped.length) {
              const activeEmail = scoped[0].email || activeLogEmail;
              setCurrentLogEmail(activeEmail);
              setSelectedLogs((old) => [...scoped, ...old].slice(0, 200));
              activeLogEmail = activeEmail;
            }
          }
          if (task.terminal) {
            if (ignoredTaskIdsRef.current.has(taskId)) return;
            setBusy(false);
            setActiveTaskId("");
            setActiveTaskMailboxIds([]);
            notify(task.status === "succeeded" ? "ok" : "fail", task.status === "succeeded" ? t.taskDone : (task.error || t.taskFailed));
            void load();
            return;
          }
        } catch (e: any) {
          failures += 1;
          if (failures >= 12) {
            if (ignoredTaskIdsRef.current.has(taskId)) return;
            const msg = template(t.taskPollLost, { error: e?.message || String(e) });
            setGlobalLogs((old) => [localLog(msg, "error"), ...old].slice(0, 200));
            notify("fail", msg);
            setBusy(false);
            setActiveTaskId("");
            setActiveTaskMailboxIds([]);
            return;
          }
        }
        await new Promise((r) => setTimeout(r, 1000));
      }
      if (ignoredTaskIdsRef.current.has(taskId)) return;
      setGlobalLogs((old) => [localLog(t.taskPollTimeout, "warning"), ...old].slice(0, 200));
      notify("fail", t.taskPollTimeout);
      setBusy(false);
      setActiveTaskId("");
      setActiveTaskMailboxIds([]);
    } finally {
      pollingTaskIdsRef.current.delete(taskId);
      ignoredTaskIdsRef.current.delete(taskId);
    }
  }
  useEffect(() => {
    if (busy && !activeTaskId) {
      setBusy(false);
      return;
    }
    if (!busy || !activeTaskId) return;
    if (!resumedTaskIdsRef.current.has(activeTaskId)) {
      resumedTaskIdsRef.current.add(activeTaskId);
      setGlobalLogs((old) => [localLog(t.taskPollRecovered), ...old].slice(0, 200));
    }
    void poll(activeTaskId, activeTaskMailboxIds);
  }, [busy, activeTaskId, activeTaskMailboxIds]);
  async function importFile(file?: File) {
    if (!file) return;
    const text = await file.text();
    try { await apiFetch("/sunny/mailboxes/import", { method: "POST", body: JSON.stringify({ lines: text }) }); notify("ok", t.importDone); void load(); } catch (e: any) { notify("fail", e.message || String(e)); }
  }
  async function exportAccounts() {
    const text = rows.map((r) => `${r.email}----${r.password || ""}----${r.client_id || ""}----${r.refresh_token || ""}`).join("\n") + "\n";
    const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
    triggerBrowserDownload(blob, "sunnyregister-chatgpt-accounts.txt");
    notify("ok", t.exportDone);
  }
  async function refreshAccountStatus(row: AnyObj) {
    const accountId = Number(row.account?.id || 0);
    if (!accountId) {
      await load();
      notify("ok", t.done);
      return;
    }
    setBusy(true);
    setGlobalLogs((old) => [localLog(`${t.refreshStatus}: ${row.email}`), ...old]);
    try {
      const res = await apiFetch("/sunny/tasks/refresh-session", { method: "POST", body: JSON.stringify({ account_ids: [accountId], concurrency: 1 }) });
      const taskId = String(res.id || res.task_id || "");
      setActiveTaskId(taskId);
      setActiveTaskMailboxIds([row.id]);
      void poll(taskId, [row.id]);
    } catch (e: any) {
      notify("fail", e.message || String(e));
      setBusy(false);
      setActiveTaskId("");
      setActiveTaskMailboxIds([]);
    }
  }
  const visibleSelected = selected.filter((id)=>rows.some((r)=>r.id === id));
  const selectedRows = rows.filter((m)=>visibleSelected.includes(m.id));
  const allChecked = pagedRows.length > 0 && pagedRows.every((r) => selected.includes(r.id));
  return <div className="space-y-5">
    <div className="grid gap-4 lg:grid-cols-2">
      <LogCard t={t} title={t.globalLogs} logs={globalLogs} busy={busy} onClear={()=>setGlobalLogs([])}/>
      <LogCard t={t} title={t.selectedLogs} logs={selectedLogs} busy={busy} onClear={()=>setSelectedLogs([])}/>
    </div>
    <Card className="sr-toolbar rounded-[18px] p-5">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-4"><h2 className="text-2xl font-bold text-slate-950 dark:text-white">ChatGPT</h2><span className="text-sm text-slate-400">{t.selected}: {visibleSelected.length}</span></div>
        <div className="flex flex-wrap gap-2">
          {busy && activeTaskId ? <button className="sr-btn sr-danger-btn" title={t.interruptTaskTip} onClick={cancelActiveTask}><X className="h-4 w-4"/>{t.interruptTask}</button> : null}
          <span title={!visibleSelected.length ? t.chooseMailbox : ""}>
            <Button className="rounded-xl bg-blue-600 px-4 text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50" onClick={() => setAutoOpen(true)} disabled={busy || visibleSelected.length === 0}><Plus className="mr-2 h-4 w-4"/>{t.autoRegister}</Button>
          </span>
          <label className="sr-btn"><Download className="h-4 w-4"/>{t.import}<input type="file" className="hidden" onChange={(e)=>importFile(e.target.files?.[0])}/></label>
          <button className="sr-btn" onClick={exportAccounts} disabled={!rows.length}><Upload className="h-4 w-4"/>{t.export}</button>
        </div>
      </div>
      <div className="mt-5 border-t border-slate-100 pt-4 dark:border-white/10">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex min-w-0 flex-1 flex-wrap gap-3">
            <div className="relative min-w-[280px] max-w-lg flex-1"><Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400"/><input className="sr-search" value={query} onChange={(e)=>setQuery(e.target.value)} placeholder={t.searchAccount} /></div>
            <SelectBox className="sr-select-like" value={groupFilter} onChange={(v)=>setGroupFilter(Number(v))} options={[{value:0,label:t.allGroups}, ...groups.map((g)=>({value:g.id,label:g.name || t.defaultGroup}))]} />
            <SelectBox className="sr-select-like" value={status} onChange={(v)=>setStatus(String(v))} options={[{value:"",label:t.allStatus}, ...MAILBOX_STATUSES.map((s)=>({value:s,label:t.statusLabels[s as keyof typeof t.statusLabels] || s}))]} />
            <SelectBox className="sr-select-like" value={planFilter} onChange={(v)=>setPlanFilter(String(v))} options={[{value:"",label:t.allPlanTypes}, ...PLAN_TYPE_OPTIONS.map((p)=>({value:p,label:formatPlanType(p)}))]} />
          </div>
          <button className="sr-text-btn" title={t.refreshList} onClick={refreshList}><RefreshCw className="h-5 w-5"/></button>
        </div>
      </div>
    </Card>
    <Card className="sr-table-card overflow-hidden rounded-[18px] p-0">
      <table className="sr-account-table"><thead><tr><th><input type="checkbox" checked={allChecked} onChange={(e)=>setSelected(e.target.checked ? Array.from(new Set([...selected, ...pagedRows.map((r)=>r.id)])) : selected.filter((id)=>!pagedRows.some((r)=>r.id===id)))}/></th><th>{t.email}</th><th>{t.mailboxGroup}</th><th>{t.status}</th><th>{t.planType}</th><th><SortTimeHeader label={t.statusChangedAt} order={timeSort} onToggle={()=>setTimeSort(nextSortOrder(timeSort))}/></th><th>{t.operation}</th></tr></thead><tbody>{rows.length ? pagedRows.map((r) => <tr key={r.id}><td><input type="checkbox" checked={selected.includes(r.id)} onChange={(e)=>setSelected(e.target.checked ? [...selected, r.id] : selected.filter((x)=>x!==r.id))}/></td><td>{r.email}</td><td>{r.group_name || t.defaultGroup}</td><td><StatusBadge t={t} status={r.status || "鏈敞鍐?} /></td><td><PlanTypeBadge value={r.account?.plan_type || r.plan_type} /></td><td>{formatDateTime(r.updated_at || r.account?.updated_at)}</td><td><button className="sr-link inline-flex items-center gap-1" title={t.refreshStatus} disabled={busy} onClick={()=>refreshAccountStatus(r)}><RefreshCw className="h-4 w-4"/>{t.refresh}</button></td></tr>) : <tr><td colSpan={7}><div className="sr-empty"><div className="sr-empty-icon"><Inbox className="h-7 w-7"/></div><div className="mt-3 text-base font-medium text-slate-900 dark:text-white">{t.noData}</div><p className="mt-2 text-sm text-slate-400">{t.noDataDesc}</p></div></td></tr>}</tbody></table>
      <PaginationBar t={t} total={total} page={safePageNo} pageSize={pageSize} setPage={setPageNo} setPageSize={setPageSize} />
    </Card>
    {autoOpen && <AutoRegisterModal t={t} busy={busy} selectedEmails={selectedRows.map((m)=>m.email)} selectedNeedPhone={selectedRows.some((m)=>!String(m.openai_rt || m.account?.openai_rt || "").trim())} concurrency={modalConcurrency} setConcurrency={setModalConcurrency} identity={identity} setIdentity={setIdentity} mode={mode} setMode={setMode} stage={stage} setStage={setStage} onClose={()=>setAutoOpen(false)} onStart={()=>createRegisterTask()} notify={notify} />}
  </div>;
}

function AutoRegisterModal({ t, busy, selectedEmails, selectedNeedPhone, concurrency, setConcurrency, identity, setIdentity, mode, setMode, stage, setStage, onClose, onStart, notify }: { t: typeof zh; busy: boolean; selectedEmails: string[]; selectedNeedPhone: boolean; concurrency: number; setConcurrency: (v:number)=>void; identity: "system"|"google"|"microsoft"; setIdentity: (v:"system"|"google"|"microsoft")=>void; mode: "protocol"|"background"|"visible"; setMode:(v:"protocol"|"background"|"visible")=>void; stage: RegisterStage; setStage:(v:RegisterStage)=>void; onClose:()=>void; onStart:()=>void; notify:(type:"ok"|"fail", text:string)=>void }) {
  const [phoneCfg, setPhoneCfg] = useState<AnyObj>({ pool_enabled: true, usable_count: 0 });
  const [reverseCfg, setReverseCfg] = useState<AnyObj>({});
  const [mailboxCfg, setMailboxCfg] = useState<AnyObj>({ pool_enabled: true });
  const [resourceLoaded, setResourceLoaded] = useState(false);
  useEffect(() => {
    let alive = true;
    Promise.all([
      apiFetch("/sunny/phones/config").catch(() => ({})),
      apiFetch("/sunny/sub2api-config").catch(() => ({})),
      apiFetch("/sunny/mailboxes/config").catch(() => ({})),
    ]).then(([phone, reverse, mailbox]) => {
      if (!alive) return;
      setPhoneCfg(phone || {});
      setReverseCfg(reverse || {});
      setMailboxCfg(mailbox || { pool_enabled: true });
      setResourceLoaded(true);
    });
    return () => { alive = false; };
  }, []);
  const identityText = identity === "system" ? t.systemMailbox : identity === "google" ? "Google" : "Microsoft";
  const modeText = mode === "protocol" ? t.protocolMode : mode === "background" ? t.backgroundMode : t.visibleMode;
  const stageText = stage === "codex_phone_bind" ? t.codexPhoneBind : stage === "import_reverse_proxy" ? t.importReverseProxy : t.registerOnly;
  const usablePhones = Number(phoneCfg.usable_count || 0);
  const poolPhoneReady = phoneCfg.pool_enabled !== false && usablePhones > 0;
  const smsbowerReady = phoneCfg.smsbower_enabled === true && !!String(phoneCfg.smsbower_api_key || "").trim();
  const smspoolReady = phoneCfg.smspool_enabled === true && !!String(phoneCfg.smspool_api_key || "").trim();
  const phoneReady = poolPhoneReady || smsbowerReady || smspoolReady;
  const sub2apiReady = reverseCfg.enabled !== false && !!String(reverseCfg.base_url || "").trim() && !!String(reverseCfg.admin_token || "").trim() && Array.isArray(reverseCfg.group_ids) && reverseCfg.group_ids.length > 0;
  const mailboxPoolReady = mailboxCfg.pool_enabled !== false;
  const googleMailboxReady = false;
  const microsoftMailboxReady = false;
  const identityValid = (identity === "system" && mailboxPoolReady) || (identity === "google" && googleMailboxReady) || (identity === "microsoft" && microsoftMailboxReady);
  const externalSmsReady = phoneCfg.external_enabled === true || phoneCfg.external_provider_enabled === true;
  const smsConfigReady = phoneReady || externalSmsReady;
  const modeValid = mode === "visible" || mode === "background";
  const registerOnlyDisabled = !identityValid;
  const stageValid = identityValid && (stage === REGISTER_ONLY || (stage === CODEX_PHONE_BIND && smsConfigReady) || (stage === IMPORT_REVERSE_PROXY && smsConfigReady && sub2apiReady));
  const startDisabled = busy || !identityValid || !modeValid || !stageValid;
  useEffect(() => {
    if (!resourceLoaded) return;
    if (identityValid && (stage === "import_reverse_proxy" && (!smsConfigReady || !sub2apiReady))) setStage(REGISTER_ONLY);
    if (identityValid && (stage === "codex_phone_bind" && !smsConfigReady)) setStage(REGISTER_ONLY);
  }, [resourceLoaded, identityValid, smsConfigReady, sub2apiReady, stage, setStage]);
  const mailboxHint = t.linkedMailboxConfig + " 路 " + (mailboxPoolReady ? t.resourceReady : t.resourceMissing);
  const phoneHint = t.linkedPhoneConfig + " 路 " + (!selectedNeedPhone ? t.existingRTReady : poolPhoneReady ? template(t.usablePhones, { count: usablePhones }) : smsbowerReady ? t.smsbowerReady : smspoolReady ? t.smspoolReady : t.resourceMissing);
  const reverseHint = t.linkedReverseConfig + " 路 " + (sub2apiReady ? t.sub2apiReady : t.sub2apiMissing);
  const codexDisabled = !identityValid || (resourceLoaded && !smsConfigReady);
  const importDisabled = !identityValid || (resourceLoaded && (!smsConfigReady || !sub2apiReady));
  const safeConcurrency = Math.max(1, Math.min(Number(concurrency) || 1, Math.max(1, selectedEmails.length)));
  return <div className="sr-modal-mask"><div className="sr-modal sr-register-modal">
    <div className="sr-modal-head"><h3>{t.autoRegisterTitle}</h3><button onClick={onClose}><X className="h-5 w-5"/></button></div>
    <div className="sr-modal-body">
      <div className="sr-step">{t.step} 1</div>
      <h4>{t.step1Title}</h4><p>{t.step1Desc}</p>
      <div className="sr-choice-grid two">
        <Choice disabled={!mailboxPoolReady} disabledMessage={t.systemMailboxPoolDisabled} active={mailboxPoolReady && identity==="system"} title={t.systemMailbox} desc={t.systemMailboxDesc} onClick={()=>{ setIdentity("system"); setStage(REGISTER_ONLY); }} onDisabledClick={(msg)=>notify("fail", msg)} />
        <Choice disabled disabledMessage={t.googleMailboxDisabled} active={false} title="Google" desc={t.googleDesc} onClick={()=>setIdentity("google")} onDisabledClick={(msg)=>notify("fail", msg)} />
        <Choice disabled disabledMessage={t.microsoftMailboxDisabled} active={false} title="Microsoft" desc={t.microsoftDesc} onClick={()=>setIdentity("microsoft")} onDisabledClick={(msg)=>notify("fail", msg)} />
      </div>
      <div className="sr-step mt-7">{t.step} 2</div>
      <h4>{t.step2Title}</h4><p>{t.step2Desc}</p>
      <div className="sr-choice-grid three">
        <Choice disabled active={mode==="protocol"} title={t.protocolMode} desc={t.protocolDesc} onClick={()=>setMode("protocol")} />
        <Choice active={mode==="background"} title={t.backgroundMode} desc={t.backgroundDesc} onClick={()=>setMode("background")} />
        <Choice active={mode==="visible"} title={t.visibleMode} desc={t.visibleDesc} onClick={()=>setMode("visible")} />
      </div>
      <div className="sr-step mt-7">{t.step} 3</div>
      <h4>{t.step3Title}</h4><p>{t.step3Desc}</p>
      <div className="sr-choice-grid three">
        <Choice disabled={registerOnlyDisabled} disabledMessage={t.registerStageUnavailable} active={identityValid && stage===REGISTER_ONLY} title={t.registerOnly} desc={t.registerOnlyDesc + "\n" + mailboxHint} onClick={()=>setStage(REGISTER_ONLY)} onDisabledClick={(msg)=>notify("fail", msg)} />
        <Choice disabled={codexDisabled} disabledMessage={!identityValid ? t.registerStageUnavailable : t.smsConfigDisabled} active={!codexDisabled && stage===CODEX_PHONE_BIND} title={t.codexPhoneBind} desc={t.codexPhoneBindDesc + "\n" + phoneHint + (codexDisabled ? " 路 " + t.stageDisabledTip : "")} onClick={()=>setStage(CODEX_PHONE_BIND)} onDisabledClick={(msg)=>notify("fail", msg)} />
        <Choice disabled={importDisabled} disabledMessage={!identityValid ? t.registerStageUnavailable : !smsConfigReady ? t.smsConfigDisabled : t.stageDisabledTip} active={!importDisabled && stage===IMPORT_REVERSE_PROXY} title={t.importReverseProxy} desc={t.importReverseProxyDesc + "\n" + phoneHint + "\n" + reverseHint + (importDisabled ? " 路 " + t.stageDisabledTip : "")} onClick={()=>setStage(IMPORT_REVERSE_PROXY)} onDisabledClick={(msg)=>notify("fail", msg)} />
      </div>
      <div className="sr-summary sr-register-summary"><div><b>{t.identityLabel}</b><span>{identityText}</span></div><div><b>{t.modeLabel}</b><span>{modeText}</span></div><div><b>{t.stageLabel}</b><span>{stageText}</span></div><div><b>{t.registerAccounts}</b><span>{selectedEmails.length}</span></div><div><b>{t.concurrency}</b><input className="sr-concurrency-input" type="number" min={1} max={Math.max(1, selectedEmails.length)} value={safeConcurrency} onChange={(e)=>setConcurrency(Math.max(1, Math.min(Number(e.target.value || 1), Math.max(1, selectedEmails.length))))}/></div><div className="sr-register-account-list">{selectedEmails.map((email)=><div key={email}>{email}</div>)}</div></div>
      <div className="sr-register-actions"><Button className="h-12 flex-1 rounded-xl bg-blue-600 text-lg text-white hover:bg-blue-700" disabled={startDisabled} onClick={onStart}>{busy ? <Loader2 className="mr-2 h-5 w-5 animate-spin"/> : null}{t.startAutoRegister}</Button><button className="sr-register-cancel" onClick={onClose}>{t.cancel}</button></div>
    </div>
  </div></div>;
}

function Choice({ active, disabled, disabledMessage, title, desc, onClick, onDisabledClick }: { active: boolean; disabled?: boolean; disabledMessage?: string; title: string; desc: string; onClick: () => void; onDisabledClick?: (message:string)=>void }) {
  return <button type="button" className={cn("sr-choice", active && "active", disabled && "disabled")} aria-disabled={disabled} onClick={() => { if (disabled) { onDisabledClick?.(disabledMessage || "Disabled"); return; } onClick(); }}><b>{title}</b><span>{desc}</span></button>;
}

function mailboxLineErrors(lines: string): string[] {
  const errors: string[] = [];
  String(lines || "").split(/\r?\n/).forEach((raw, index) => {
    const line = raw.trim();
    if (!line) return;
    const parts = line.split("----").map((x) => x.trim());
    if (parts.length < 4 || !parts[0] || !parts[0].includes("@") || !parts[2] || !parts[3]) {
      errors.push(`Line ${index + 1}: email----password----client_id----refresh_token`);
    }
  });
  return errors;
}

function MailboxConfig({ t, notify }: { t: typeof zh; notify: (type: "ok" | "fail", text: string) => void }) {
  const [items,setItems]=useCachedState<AnyObj[]>("mailbox.items", []);
  const [groups,setGroups]=useCachedState<AnyObj[]>("mailbox.groups", []);
  const [page,setPage]=useCachedState("mailbox.page", 1);
  const [pageSize,setPageSize]=useCachedState("mailbox.pageSize", 10);
  const [total,setTotal]=useCachedState("mailbox.total", 0);
  const [query,setQuery]=useCachedState("mailbox.query", "");
  const [groupFilter,setGroupFilter]=useCachedState("mailbox.groupFilter", 0);
  const [statusFilter,setStatusFilter]=useCachedState("mailbox.statusFilter", "");
  const [timeSort,setTimeSort]=useCachedState<SortOrder>("mailbox.timeSort", "desc");
  const [selected,setSelected]=useCachedState<number[]>("mailbox.selected", []);
  const [importOpen,setImportOpen]=useState(false);
  const [editing,setEditing]=useState<AnyObj|null>(null);
  const [batchEditing,setBatchEditing]=useState(false);
  const [mailboxForMail,setMailboxForMail]=useState<AnyObj|null>(null);
  const [mailboxCfg,setMailboxCfg]=useCachedState<AnyObj>("mailbox.config",{pool_enabled:true});
  const load=async()=>{
    const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
    if (query.trim()) params.set("q", query.trim());
    if (groupFilter) params.set("group_id", String(groupFilter));
    if (statusFilter) params.set("status", statusFilter);
    params.set("sort_by", "updated_at");
    params.set("sort_order", timeSort);
    const [m,g]=await Promise.all([apiFetch(`/sunny/mailboxes?${params.toString()}`),apiFetch("/sunny/mailbox-groups")]);
    setItems(m.items||[]);
    setTotal(m.total||0);
    setGroups(g.items||[]);
  };
  useEffect(()=>{void load()},[page, query, groupFilter, statusFilter, timeSort, pageSize]);
  useEffect(()=>{apiFetch("/sunny/mailboxes/config").then((cfg)=>setMailboxCfg(cfg || {pool_enabled:true})).catch(()=>{})},[]);
  useEffect(()=>{setPage(1)},[query, groupFilter, statusFilter, timeSort, pageSize]);
  useEffect(()=>{const pages=pageCount(total,pageSize); if(page>pages) setPage(pages);},[total,pageSize,page]);
  async function run(label:string, fn:()=>Promise<any>){try{await fn();notify("ok",label);void load()}catch(e:any){notify("fail",e.message||String(e))}}
  async function deleteMailbox(m: AnyObj) {
    await run(t.done,()=>apiFetch(`/sunny/mailboxes/${m.id}`,{method:"DELETE"}));
  }
  async function batchDelete(){
    if (!selected.length) return;
    await run(t.done, async()=>{ await Promise.all(selected.map((id)=>apiFetch(`/sunny/mailboxes/${id}`,{method:"DELETE"}))); setSelected([]); });
  }
  async function toggleMailboxPoolEnabled() {
    const next = !(mailboxCfg.pool_enabled !== false);
    try {
      const saved = await apiFetch("/sunny/mailboxes/config", { method:"PUT", body: JSON.stringify({ ...mailboxCfg, pool_enabled: next }) });
      setMailboxCfg(saved || { pool_enabled: next });
      notify("ok", t.done);
    } catch(e:any) { notify("fail", e.message || String(e)); }
  }
  const allChecked = items.length > 0 && items.every((m)=>selected.includes(m.id));
  const mailboxPoolEnabled = mailboxCfg.pool_enabled !== false;
  return <div className="space-y-4">
    <Card className="sr-sms-provider-card sr-mailbox-provider-card rounded-[24px] p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-bold">{t.mailboxPoolName}</h2>
        </div>
        <button type="button" aria-label={t.mailboxPoolGlobalSwitch} title={t.mailboxPoolSwitchTip} className={cn("sr-switch-only", mailboxPoolEnabled && "on")} onClick={toggleMailboxPoolEnabled}>
          <span />
        </button>
      </div>
      {mailboxPoolEnabled && <div className="sr-mailbox-expanded mt-5 space-y-4">
        <div className="sr-toolbar sr-toolbar-compact sr-mailbox-toolbar sr-mailbox-inner-toolbar rounded-[18px] p-4">
          <div className="sr-mailbox-toolbar-row flex flex-nowrap items-center justify-between gap-2">
            <div className="sr-mailbox-filters flex min-w-0 flex-nowrap gap-2">
              <div className="sr-mailbox-search relative"><Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400"/><input className="sr-search" value={query} onChange={(e)=>setQuery(e.target.value)} placeholder={t.queryMailbox}/></div>
              <SelectBox className="sr-select-like" value={groupFilter} onChange={(v)=>setGroupFilter(Number(v))} options={[{value:0,label:t.allGroups}, ...groups.map((g)=>({value:g.id,label:g.name || t.defaultGroup}))]} />
              <SelectBox className="sr-select-like" value={statusFilter} onChange={(v)=>setStatusFilter(String(v))} options={[{value:"",label:t.allStatus}, ...MAILBOX_STATUSES.map((s)=>({value:s,label:t.statusLabels[s as keyof typeof t.statusLabels] || s}))]} />
            </div>
            <div className="sr-mailbox-actions flex flex-nowrap gap-2">{selected.length > 0 && <ConfirmBubble message={t.confirmBatchDeleteMailbox} detail={`${selected.length} ${t.selected}`} onConfirm={batchDelete}><Button variant="outline" className="rounded-xl border-red-200 text-red-500">{t.batchDelete} ({selected.length})</Button></ConfirmBubble>}{selected.length > 0 && <Button variant="outline" className="rounded-xl border-emerald-200 text-emerald-700" onClick={()=>setBatchEditing(true)}>{t.batchEdit} ({selected.length})</Button>}<button className="sr-text-btn" onClick={load}><RefreshCw className="h-4 w-4"/>{t.refresh}</button><Button className="rounded-xl bg-emerald-600 px-4 text-white hover:bg-emerald-700" onClick={()=>setImportOpen(true)}><Download className="mr-2 h-4 w-4"/>{t.importMailboxes}</Button></div>
          </div>
        </div>
        <div className="sr-table-card sr-mailbox-table-panel overflow-hidden rounded-[18px] p-0">
          <table className="sr-account-table">
            <thead><tr><th><input type="checkbox" checked={allChecked} onChange={(e)=>setSelected(e.target.checked ? items.map((m)=>m.id) : [])}/></th><th>{t.mailbox}</th><th>{t.importToGroup}</th><th>{t.status}</th><th>{t.planType}</th><th>{t.enabled}</th><th><SortTimeHeader label={t.updatedAt} order={timeSort} onToggle={()=>setTimeSort(nextSortOrder(timeSort))}/></th><th>{t.actions}</th></tr></thead>
            <tbody>{items.length ? items.map((m)=><tr key={m.id}>
              <td><input type="checkbox" checked={selected.includes(m.id)} onChange={(e)=>setSelected(e.target.checked ? [...selected,m.id] : selected.filter((id)=>id!==m.id))}/></td>
              <td><div className="font-semibold">{m.email}</div></td>
              <td><SelectBox className="sr-mini-select-like" value={m.group_id || 0} onChange={(v)=>run(t.done,()=>apiFetch(`/sunny/mailboxes/${m.id}`,{method:"PUT",body:JSON.stringify({group_id:Number(v)})}))} options={groups.map((g)=>({value:g.id,label:g.name || t.defaultGroup}))} /></td>
              <td><StatusBadge t={t} status={m.status || "鏈敞鍐?} /></td>
              <td><PlanTypeBadge value={m.plan_type} /></td>
              <td><button className={cn("sr-toggle", m.enabled && "on")} onClick={()=>run(t.done,()=>apiFetch(`/sunny/mailboxes/${m.id}`,{method:"PUT",body:JSON.stringify({enabled:!m.enabled})}))}>{m.enabled ? "ON" : "OFF"}</button></td>
              <td>{formatDateTime(m.updated_at)}</td>
              <td><div className="flex flex-wrap gap-2"><button className="sr-link" onClick={()=>setMailboxForMail(m)}>{t.queryMail}</button><button className="sr-link" onClick={()=>setEditing(m)}>{t.edit}</button><ConfirmBubble message={t.confirmDeleteMailbox} detail={m.email || ""} onConfirm={()=>deleteMailbox(m)}><button className="sr-link text-red-500">{t.delete}</button></ConfirmBubble></div></td>
            </tr>) : <tr><td colSpan={8}><div className="sr-empty"><div className="sr-empty-icon"><Inbox className="h-7 w-7"/></div><div className="mt-3 text-base font-medium text-slate-900 dark:text-white">{t.noMailbox}</div><p className="mt-2 text-sm text-slate-400">{t.noMailboxDesc}</p></div></td></tr>}</tbody>
          </table>
          <PaginationBar t={t} total={total} page={page} pageSize={pageSize} setPage={setPage} setPageSize={setPageSize} />
        </div>
      </div>}
    </Card>
    {importOpen && <MailboxImportModal t={t} groups={groups} onClose={()=>setImportOpen(false)} onImported={()=>{setImportOpen(false); notify("ok",t.done); void load();}} notify={notify}/>}
    {editing && <MailboxEditModal t={t} mailbox={editing} groups={groups} onClose={()=>setEditing(null)} onSaved={()=>{setEditing(null); notify("ok",t.done); void load();}} notify={notify}/>}
    {batchEditing && <MailboxBatchEditModal t={t} selected={selected} groups={groups} onClose={()=>setBatchEditing(false)} onSaved={()=>{setBatchEditing(false); setSelected([]); notify("ok",t.done); void load();}} notify={notify}/>}
    {mailboxForMail && <MailboxMailModal t={t} mailbox={mailboxForMail} onClose={()=>setMailboxForMail(null)} notify={notify}/>}
  </div>;
}

function StatusBadge({ t, status }: { t: typeof zh; status: string }) {
  const map: Record<string,string> = {
    "鏈敞鍐?: "gray",
    "宸叉敞鍐?: "blue",
    "registered": "blue",
    "宸叉帴鐮?: "green",
    "PLUS璇曠敤涓?: "violet",
    "宸插皝绂?: "red",
    "闇€浜岄獙": "amber",
    "娉ㄥ唽涓?: "amber",
    "鐧诲綍鍒锋柊": "blue",
    "澶辫触": "red",
    "failed": "red",
    "绂佺敤": "red",
  };
  return <span className={cn("sr-status", `sr-status-${map[status] || "gray"}`)}>{t.statusLabels[status as keyof typeof t.statusLabels] || status}</span>;
}

function formatPlanType(value: any) {
  const text = String(value || "").trim();
  if (!text || text === "-") return "-";
  const lower = text.toLowerCase();
  return lower.charAt(0).toUpperCase() + lower.slice(1);
}

function PlanTypeBadge({ value }: { value: any }) {
  const label = formatPlanType(value);
  if (label === "-") return <span className="text-slate-400">-</span>;
  return <span className="sr-plan-badge">{label}</span>;
}

function MailboxEditModal({ t, mailbox, groups, onClose, onSaved, notify }: { t: typeof zh; mailbox: AnyObj; groups: AnyObj[]; onClose:()=>void; onSaved:()=>void; notify:(type:"ok"|"fail", text:string)=>void }) {
  const [form,setForm]=useState<AnyObj>(()=>({...mailbox, plan_type: mailbox.account_type || mailbox.plan_type || "free"}));
  async function save() {
    if (!String(form.email || "").includes("@") || !String(form.client_id || "").trim() || !String(form.refresh_token || "").trim()) {
      notify("fail", t.validationFailed);
      return;
    }
    try {
      await apiFetch(`/sunny/mailboxes/${mailbox.id}`, { method:"PUT", body: JSON.stringify({
        email: form.email, password: form.password, client_id: form.client_id, refresh_token: form.refresh_token,
        access_token: form.access_token, group_id: Number(form.group_id), status: form.status, plan_type: form.plan_type || form.account_type, enabled: !!form.enabled,
      })});
      onSaved();
    } catch(e:any) { notify("fail", e.message || String(e)); }
  }
  return <div className="sr-modal-mask"><div className="sr-modal sr-mailbox-modal">
    <div className="sr-modal-head"><h3>{t.edit} {t.mailbox}</h3><button onClick={onClose}><X className="h-5 w-5"/></button></div>
    <div className="sr-modal-body space-y-4">
      <div className="grid gap-4 md:grid-cols-2">
        <div><Label>{t.mailboxName}</Label><Input value={form.email||""} onChange={(e)=>setForm({...form,email:e.target.value})}/></div>
        <div><Label>{t.password}</Label><Input value={form.password||""} onChange={(e)=>setForm({...form,password:e.target.value})}/></div>
        <div><Label>{t.clientId}</Label><Input value={form.client_id||""} onChange={(e)=>setForm({...form,client_id:e.target.value})}/></div>
        <div><Label>{t.refreshToken}</Label><Input value={form.refresh_token||""} onChange={(e)=>setForm({...form,refresh_token:e.target.value})}/></div>
        <div><Label>{t.openaiAccessToken}</Label><Input value={form.access_token||""} onChange={(e)=>setForm({...form,access_token:e.target.value})}/></div>
        <div><Label>{t.importToGroup}</Label><SelectBox value={form.group_id||0} onChange={(v)=>setForm({...form,group_id:Number(v)})} options={groups.map((g)=>({value:g.id,label:g.name || t.defaultGroup}))} /></div>
        <div><Label>{t.status}</Label><SelectBox value={form.status||MAILBOX_STATUSES[0]} onChange={(v)=>setForm({...form,status:String(v)})} options={MAILBOX_STATUSES.map((s)=>({value:s,label:t.statusLabels[s as keyof typeof t.statusLabels] || s}))} /></div>
        <div><Label>{t.planType}</Label><SelectBox value={form.plan_type === "-" ? "free" : (form.plan_type || form.account_type || "free")} onChange={(v)=>setForm({...form,plan_type:String(v),account_type:String(v)})} options={PLAN_TYPE_OPTIONS.map((p)=>({value:p,label:formatPlanType(p)}))} /></div>
        <div className="flex items-end"><button className={cn("sr-toggle", form.enabled && "on")} onClick={()=>setForm({...form,enabled:!form.enabled})}>{form.enabled ? "ON" : "OFF"}</button></div>
      </div>
    </div>
    <div className="sr-modal-foot"><button onClick={onClose}>{t.cancel}</button><Button className="ml-3 rounded-xl bg-emerald-600 px-6 !text-white hover:bg-emerald-700" onClick={save}>{t.save}</Button></div>
  </div></div>;
}

function MailboxBatchEditModal({ t, selected, groups, onClose, onSaved, notify }: { t: typeof zh; selected: number[]; groups: AnyObj[]; onClose:()=>void; onSaved:()=>void; notify:(type:"ok"|"fail", text:string)=>void }) {
  const [form,setForm]=useState<AnyObj>({ group_id: groups[0]?.id || 0, status: MAILBOX_STATUSES[0], plan_type: "free", enabled: true });
  async function save() {
    if (!selected.length) {
      notify("fail", t.chooseMailbox);
      return;
    }
    try {
      const body = { group_id:Number(form.group_id), status:String(form.status), plan_type:String(form.plan_type), enabled:!!form.enabled };
      await Promise.all(selected.map((id)=>apiFetch(`/sunny/mailboxes/${id}`, { method:"PUT", body:JSON.stringify(body) })));
      onSaved();
    } catch(e:any) {
      notify("fail", e.message || String(e));
    }
  }
  return <div className="sr-modal-mask"><div className="sr-modal sr-mailbox-modal">
    <div className="sr-modal-head"><h3>{t.batchEditMailboxTitle}</h3><button onClick={onClose}><X className="h-5 w-5"/></button></div>
    <div className="sr-modal-body space-y-4">
      <div className="rounded-2xl border border-emerald-200 bg-emerald-50/70 px-4 py-3 text-sm font-bold text-emerald-700 dark:border-emerald-400/20 dark:bg-emerald-400/10 dark:text-emerald-200">{t.selected}: {selected.length}</div>
      <div className="grid gap-4 md:grid-cols-2">
        <div><Label>{t.importToGroup}</Label><SelectBox value={form.group_id||0} onChange={(v)=>setForm({...form,group_id:Number(v)})} options={groups.map((g)=>({value:g.id,label:g.name || t.defaultGroup}))} /></div>
        <div><Label>{t.status}</Label><SelectBox value={form.status||MAILBOX_STATUSES[0]} onChange={(v)=>setForm({...form,status:String(v)})} options={MAILBOX_STATUSES.map((s)=>({value:s,label:t.statusLabels[s as keyof typeof t.statusLabels] || s}))} /></div>
        <div><Label>{t.planType}</Label><SelectBox value={form.plan_type||"free"} onChange={(v)=>setForm({...form,plan_type:String(v)})} options={PLAN_TYPE_OPTIONS.map((p)=>({value:p,label:formatPlanType(p)}))} /></div>
        <div><Label>{t.enabled}</Label><button className={cn("sr-toggle", form.enabled && "on")} onClick={()=>setForm({...form,enabled:!form.enabled})}>{form.enabled ? "ON" : "OFF"}</button></div>
      </div>
    </div>
    <div className="sr-modal-foot"><button onClick={onClose}>{t.cancel}</button><Button className="ml-3 rounded-xl bg-emerald-600 px-6 !text-white hover:bg-emerald-700" onClick={save}><Save className="mr-2 h-4 w-4"/>{t.applyToSelected}</Button></div>
  </div></div>;
}

function MailboxMailModal({ t, mailbox, onClose, notify }: { t: typeof zh; mailbox: AnyObj; onClose:()=>void; notify:(type:"ok"|"fail", text:string)=>void }) {
  const [items,setItems]=useState<AnyObj[]>([]);
  const [selected,setSelected]=useState(0);
  const [loading,setLoading]=useState(false);
  const [limit,setLimit]=useState(5);
  async function load() {
    setLoading(true);
    try {
      const r = await apiFetch(`/sunny/mailboxes/${mailbox.id}/latest-mail`, { method:"POST", body: JSON.stringify({ limit }) });
      const list = Array.isArray(r.items) ? r.items : (r.empty ? [] : [r]);
      setItems(list);
      setSelected(0);
      notify("ok", t.done);
    } catch(e:any) { notify("fail", e.message || String(e)); }
    finally { setLoading(false); }
  }
  useEffect(()=>{void load()},[]);
  const mail = items[selected] || {};
  return <div className="sr-modal-mask"><div className="sr-modal sr-mail-modal">
    <div className="sr-mail-head">
      <div className="sr-current-mail">{t.currentMailbox}: <b>{mailbox.email}</b></div>
      <div className="sr-mail-actions">
        <span className="sr-mail-count-label">{t.mailFetchCount}</span>
        <SelectBox className="sr-mail-count-select" value={limit} onChange={(v)=>setLimit(Number(v))} options={[5,10,20,50].map((n)=>({value:n,label:`${n}${t.mailFetchCountSuffix}`}))} />
        <Button className="rounded-xl bg-black px-5 !text-white hover:bg-slate-800" onClick={load} disabled={loading}>{loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin"/> : <Inbox className="mr-2 h-4 w-4"/>}{t.getMail}</Button>
        <button onClick={onClose}><X className="h-5 w-5"/></button>
      </div>
    </div>
    <div className="sr-mail-layout">
      <aside className="sr-mail-list">
        <div className="sr-mail-title">{t.mailList} <span>({items.length})</span></div>
        {items.length ? items.map((m,i)=><button key={`${m.id || i}`} className={cn("sr-mail-item", i===selected && "active")} onClick={()=>setSelected(i)}>
          <div className="sr-mail-from">{m.from || "-"}</div>
          <div className="sr-mail-subject">{m.subject || "(no subject)"}</div>
          <p>{m.body_preview || m.body || ""}</p>
          <div className="sr-mail-tags"><span>{m.folder || t.inbox}</span>{m.otp ? <span>OTP {m.otp}</span> : null}</div>
        </button>) : <div className="sr-empty !min-h-[360px]"><Inbox className="h-8 w-8 text-slate-400"/><p>{t.emptyMail}</p></div>}
      </aside>
      <section className="sr-mail-detail">
        {items.length ? <>
          <h2>{mail.subject || "(no subject)"}</h2>
          <div className="sr-mail-meta"><span>{t.sender}</span><b>{mail.from || "-"}</b><span>{t.receiver}</span><b>{mail.to || mailbox.email}</b><span>{t.time}</span><b>{mail.date || "-"}</b></div>
          <div className="sr-mail-content">
            {mail.raw_html && /<html|<body|<div|<p|<table/i.test(String(mail.raw_html)) ? <iframe title="mail-content" sandbox="" srcDoc={String(mail.raw_html)} /> : <pre>{mail.body || mail.body_preview || ""}</pre>}
          </div>
        </> : <div className="sr-empty"><Inbox className="h-10 w-10 text-slate-400"/><p>{t.emptyMail}</p></div>}
      </section>
    </div>
  </div></div>;
}

function MailboxImportModal({ t, groups, onClose, onImported, notify }: { t: typeof zh; groups: AnyObj[]; onClose:()=>void; onImported:()=>void; notify:(type:"ok"|"fail", text:string)=>void }) {
  const [mode,setMode]=useState<"file"|"manual">("file");
  const [lines,setLines]=useState("");
  const [groupId,setGroupId]=useState<number>(Number(groups[0]?.id || 0));
  const [localGroups,setLocalGroups]=useState<AnyObj[]>(groups);
  const [adding,setAdding]=useState(false);
  const [newGroup,setNewGroup]=useState("");
  const [drag,setDrag]=useState(false);
  const errors = mailboxLineErrors(lines);
  const validCount = lines.split(/\r?\n/).filter((x)=>x.trim()).length - errors.length;
  async function pick(file?: File) { if (!file) return; setLines(await file.text()); setMode("file"); }
  async function createGroup() {
    const name = newGroup.trim();
    if (!name) return;
    try {
      const g = await apiFetch("/sunny/mailbox-groups",{method:"POST",body:JSON.stringify({name})});
      const next = [...localGroups, g];
      setLocalGroups(next); setGroupId(g.id); setAdding(false); setNewGroup("");
      notify("ok", t.done);
    } catch(e:any) { notify("fail", e.message || String(e)); }
  }
  async function submit() {
    const trimmed = lines.trim();
    if (!trimmed) { notify("fail", t.fillOrChooseMailboxFile); return; }
    if (errors.length) { notify("fail", `${t.validationFailed}: ${errors[0]}`); return; }
    try {
      await apiFetch("/sunny/mailboxes/import",{method:"POST",body:JSON.stringify({lines:trimmed,group_id:groupId})});
      onImported();
    } catch(e:any) { notify("fail", e.message || String(e)); }
  }
  return <div className="sr-modal-mask"><div className="sr-modal sr-mailbox-modal">
    <div className="sr-modal-head"><h3>{t.importMailboxes}</h3><button onClick={onClose}><X className="h-5 w-5"/></button></div>
    <div className="sr-modal-body space-y-5">
      <div className="grid gap-3 md:grid-cols-[1fr_220px_auto]">
        <div><Label>{t.importToGroup}</Label><SelectBox value={groupId} onChange={(v)=>setGroupId(Number(v))} options={localGroups.map((g)=>({value:g.id,label:g.name || t.defaultGroup}))} /></div>
        <div className="flex items-end">{adding ? <Input autoFocus placeholder={t.enterGroup} value={newGroup} onChange={(e)=>setNewGroup(e.target.value)} onKeyDown={(e)=>{if(e.key==="Enter") void createGroup(); if(e.key==="Escape") setAdding(false)}}/> : <Button variant="outline" className="h-11 w-full rounded-full" onClick={()=>setAdding(true)}><Plus className="mr-2 h-4 w-4"/>{t.addGroup}</Button>}</div>
      </div>
      <div className="sr-import-tabs"><button className={cn(mode==="file"&&"active")} onClick={()=>setMode("file")}>{t.fileImport}</button><button className={cn(mode==="manual"&&"active")} onClick={()=>setMode("manual")}>{t.manualImport}</button></div>
      {mode==="file" ? <label className={cn("sr-drop-zone", drag && "drag")} onDragOver={(e)=>{e.preventDefault();setDrag(true)}} onDragLeave={()=>setDrag(false)} onDrop={(e)=>{e.preventDefault();setDrag(false);void pick(e.dataTransfer.files?.[0])}}>
        <Download className="h-8 w-8"/><span>{t.dragFile}</span><small>{lines ? `${validCount} valid line(s), ${errors.length} error(s)` : "TXT / CSV"}</small><input type="file" className="hidden" onChange={(e)=>pick(e.target.files?.[0])}/>
      </label> : <Textarea className="min-h-56 rounded-2xl" value={lines} onChange={(e)=>setLines(e.target.value)} placeholder="email----password----client_id----refresh_token"/>}
      {lines ? <div className={cn("sr-validation", errors.length ? "bad" : "ok")}><b>{errors.length ? t.validationFailed : t.validationOk}</b><span>{validCount} valid / {errors.length} error</span>{errors.slice(0,4).map((e)=><div key={e}>{e}</div>)}</div> : null}
    </div>
    <div className="sr-modal-foot"><button onClick={onClose}>{t.cancel}</button><Button className="ml-3 rounded-xl bg-emerald-600 px-6 !text-white hover:bg-emerald-700" disabled={!lines.trim() || errors.length>0 || !groupId} onClick={submit}>{t.import}</Button></div>
  </div></div>;
}

const PHONE_STATUS_OPTIONS = ["enabled", "disabled"];

function phoneLineErrors(lines: string, formatLabel: string) {
  const errors: string[] = [];
  lines.split(/\r?\n/).forEach((raw, index) => {
    const line = raw.trim();
    if (!line) return;
    const parts = line.split("----");
    if (parts.length !== 2 || !parts[0].trim().startsWith("+") || !parts[1].trim().toLowerCase().startsWith("http")) {
      errors.push(`Line ${index + 1}: ${formatLabel}`);
    }
  });
  return errors;
}

function phoneStatusText(t: typeof zh, status: string) {
  return status === "disabled" ? t.phoneStatusDisabled : t.phoneStatusEnabled;
}

function providerOptionLabel(opt: AnyObj) {
  const value = String(opt.value ?? "");
  const label = String(opt.label ?? value);
  return label && label !== value ? `${value} 路 ${label}` : value;
}

function ProviderOptionSelect({ value, onChange, options, placeholder, className }: { value: string; onChange: (v: string) => void; options: AnyObj[]; placeholder: string; className?: string }) {
  const normalized = options.map((opt)=>({ value: String(opt.value ?? ""), label: providerOptionLabel(opt) })).filter((x)=>x.value);
  const hasValue = normalized.some((x)=>x.value === String(value));
  const merged = hasValue || !value ? normalized : [{ value, label: value }, ...normalized];
  return <SelectBox className={className} value={value || ""} onChange={(v)=>onChange(String(v))} options={merged.length ? merged : [{ value: value || "", label: value || placeholder }]} />;
}

function PhoneConfig({ t, notify }: { t: typeof zh; notify: (type: "ok" | "fail", text: string) => void }) {
  const [items,setItems]=useCachedState<AnyObj[]>("phone.items",[]);
  const [total,setTotal]=useCachedState("phone.total",0);
  const [query,setQuery]=useCachedState("phone.query","");
  const [statusFilter,setStatusFilter]=useCachedState("phone.status","");
  const [countFilter,setCountFilter]=useCachedState("phone.count","all");
  const [timeSort,setTimeSort]=useCachedState<SortOrder>("phone.timeSort","desc");
  const [selected,setSelected]=useCachedState<number[]>("phone.selected",[]);
  const [page,setPage]=useCachedState("phone.page",1);
  const [pageSize,setPageSize]=useCachedState("phone.pageSize",10);
  const [phoneCfg,setPhoneCfg]=useCachedState<AnyObj>("phone.config",{pool_enabled:true, smsbower_enabled:false, smsbower_base_url:"https://smsbower.page/stubs/handler_api.php", smsbower_default_country:"187", smsbower_default_service:"dr", smsbower_max_price:-1, smspool_enabled:false, smspool_base_url:"https://api.smspool.net", smspool_default_country:"1", smspool_default_service:"OpenAI", smspool_max_price:-1});
  const [savedPhoneCfg,setSavedPhoneCfg]=useState<AnyObj|null>(null);
  const [smsCheck,setSmsCheck]=useState("");
  const [smsPoolCheck,setSmsPoolCheck]=useState("");
  const [smsOptions,setSmsOptions]=useCachedState<AnyObj>("phone.providerOptions",{});
  const [editing,setEditing]=useState<AnyObj|null>(null);
  const [importOpen,setImportOpen]=useState(false);
  const load=async()=>{
    const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
    if (query.trim()) params.set("q", query.trim());
    if (statusFilter) params.set("status", statusFilter);
    if (countFilter !== "all") params.set("count", countFilter);
    params.set("sort_by", "last_used_at");
    params.set("sort_order", timeSort);
    const data = await apiFetch(`/sunny/phones?${params.toString()}`);
    setItems(data.items || []);
    setTotal(data.total || 0);
  };
  useEffect(()=>{void load()},[page, query, statusFilter, countFilter, timeSort, pageSize]);
  useEffect(()=>{apiFetch("/sunny/phones/config").then((cfg)=>{ const next = cfg || {pool_enabled:true}; setPhoneCfg(next); setSavedPhoneCfg(next); }).catch(()=>{})},[]);
  useEffect(()=>{setPage(1)},[query, statusFilter, countFilter, timeSort, pageSize]);
  useEffect(()=>{const pages=pageCount(total,pageSize); if(page>pages) setPage(pages);},[total,pageSize,page]);
  async function loadProviderOptions(provider: "smsbower"|"smspool", kind: "countries"|"services", refresh=false, country="") {
    const key = `${provider}_${kind}_${country || "all"}`;
    try {
      const res = await apiFetch("/sunny/phones/provider-options", { method:"POST", body: JSON.stringify({ ...phoneCfg, provider, kind, country, refresh }) });
      setSmsOptions((old: AnyObj)=>({ ...old, [key]: res.items || [] }));
      if (refresh) notify("ok", t.refreshDone);
    } catch(e:any) {
      if (refresh) notify("fail", e.message || String(e));
    }
  }
  const optionsFor = (provider: "smsbower"|"smspool", kind: "countries"|"services", country="") => smsOptions[`${provider}_${kind}_${country || "all"}`] || [];
  useEffect(()=>{ if (phoneCfg.smsbower_enabled === true) { void loadProviderOptions("smsbower","countries"); void loadProviderOptions("smsbower","services", false, String(phoneCfg.smsbower_default_country || "")); } },[phoneCfg.smsbower_enabled, phoneCfg.smsbower_default_country]);
  useEffect(()=>{ if (phoneCfg.smspool_enabled === true) { void loadProviderOptions("smspool","countries"); void loadProviderOptions("smspool","services", false, String(phoneCfg.smspool_default_country || "")); } },[phoneCfg.smspool_enabled, phoneCfg.smspool_default_country]);
  async function run(label:string, fn:()=>Promise<any>){
    try{await fn(); notify("ok",label); void load();}
    catch(e:any){notify("fail",e.message||String(e));}
  }
  async function deletePhone(p: AnyObj) {
    await run(t.done,()=>apiFetch(`/sunny/phones/${p.id}`,{method:"DELETE"}));
  }
  async function batchDelete(){
    if (!selected.length) return;
    await run(t.done, async()=>{ await Promise.all(selected.map((id)=>apiFetch(`/sunny/phones/${id}`,{method:"DELETE"}))); setSelected([]); });
  }
  const SMSBOWER_CONFIG_KEYS = ["smsbower_base_url", "smsbower_api_key", "smsbower_default_country", "smsbower_default_service", "smsbower_max_price"];
  const SMSPOOL_CONFIG_KEYS = ["smspool_base_url", "smspool_api_key", "smspool_default_country", "smspool_default_service", "smspool_max_price"];
  const pickConfig = (cfg: AnyObj | null | undefined, keys: string[]) => {
    const out: AnyObj = {};
    keys.forEach((key)=>{ out[key] = cfg?.[key] ?? ""; });
    return out;
  };
  const configChanged = (keys: string[]) => {
    if (!savedPhoneCfg) return false;
    return JSON.stringify(pickConfig(phoneCfg, keys)) !== JSON.stringify(pickConfig(savedPhoneCfg, keys));
  };
  const smsbowerDirty = configChanged(SMSBOWER_CONFIG_KEYS);
  const smspoolDirty = configChanged(SMSPOOL_CONFIG_KEYS);
  const mergeSavedProviderFields = (saved: AnyObj, keys: string[]) => {
    const patch = pickConfig(saved, keys);
    setSavedPhoneCfg(saved);
    setPhoneCfg((current: AnyObj)=>({
      ...current,
      ...patch,
      pool_enabled: saved.pool_enabled,
      smsbower_enabled: saved.smsbower_enabled,
      smspool_enabled: saved.smspool_enabled,
      usable_count: saved.usable_count ?? current.usable_count,
      total_count: saved.total_count ?? current.total_count,
    }));
  };
  async function savePhoneSwitch(key: "pool_enabled" | "smsbower_enabled" | "smspool_enabled", next: boolean) {
    const before = phoneCfg;
    setPhoneCfg((current: AnyObj)=>({ ...current, [key]: next }));
    try {
      const persistedBase = savedPhoneCfg || before;
      const saved = await apiFetch("/sunny/phones/config", { method:"PUT", body: JSON.stringify({ ...persistedBase, [key]: next }) });
      setSavedPhoneCfg(saved || { ...persistedBase, [key]: next });
      setPhoneCfg((current: AnyObj)=>({ ...current, [key]: next, usable_count: saved?.usable_count ?? current.usable_count, total_count: saved?.total_count ?? current.total_count }));
      notify("ok", t.done);
    } catch(e:any) {
      setPhoneCfg(before);
      notify("fail", e.message || String(e));
    }
  }
  async function togglePoolEnabled() {
    const next = !(phoneCfg.pool_enabled !== false);
    await savePhoneSwitch("pool_enabled", next);
  }
  async function toggleSMSBowerEnabled() {
    await savePhoneSwitch("smsbower_enabled", !(phoneCfg.smsbower_enabled === true));
  }
  async function toggleSMSPoolEnabled() {
    await savePhoneSwitch("smspool_enabled", !(phoneCfg.smspool_enabled === true));
  }
  async function saveSMSBowerConfig() {
    if (!smsbowerDirty) return;
    try {
      const body = { ...(savedPhoneCfg || phoneCfg), ...pickConfig(phoneCfg, SMSBOWER_CONFIG_KEYS), smsbower_enabled: phoneCfg.smsbower_enabled === true };
      const saved = await apiFetch("/sunny/phones/config", { method:"PUT", body: JSON.stringify(body) });
      mergeSavedProviderFields(saved || body, SMSBOWER_CONFIG_KEYS);
      notify("ok", t.smsbowerSaved);
    } catch(e:any) { notify("fail", e.message || String(e)); }
  }
  async function checkSMSBower() {
    setSmsCheck(t.checking);
    try {
      const res = await apiFetch("/sunny/phones/smsbower/check", { method:"POST", body: JSON.stringify(phoneCfg) });
      const text = template(t.smsbowerBalance, { balance: res.balance || res.raw || "-" });
      setSmsCheck(text);
      notify("ok", text);
    } catch(e:any) {
      const msg = e.message || String(e);
      setSmsCheck(msg);
      notify("fail", msg);
    }
  }
  async function saveSMSPoolConfig() {
    if (!smspoolDirty) return;
    try {
      const body = { ...(savedPhoneCfg || phoneCfg), ...pickConfig(phoneCfg, SMSPOOL_CONFIG_KEYS), smspool_enabled: phoneCfg.smspool_enabled === true };
      const saved = await apiFetch("/sunny/phones/config", { method:"PUT", body: JSON.stringify(body) });
      mergeSavedProviderFields(saved || body, SMSPOOL_CONFIG_KEYS);
      notify("ok", t.smspoolSaved);
    } catch(e:any) { notify("fail", e.message || String(e)); }
  }
  async function checkSMSPool() {
    setSmsPoolCheck(t.checking);
    try {
      const res = await apiFetch("/sunny/phones/smspool/check", { method:"POST", body: JSON.stringify(phoneCfg) });
      const text = template(t.smspoolBalance, { balance: res.balance || res.raw || "-" });
      setSmsPoolCheck(text);
      notify("ok", text);
    } catch(e:any) {
      const msg = e.message || String(e);
      setSmsPoolCheck(msg);
      notify("fail", msg);
    }
  }
  const allChecked = items.length > 0 && items.every((p)=>selected.includes(p.id));
  const countOptions = [{value:"all",label:t.allCount}, ...[0,1,2,3].map((n)=>({value:String(n),label:`${n} ${t.usedCount}`}))];
  const poolEnabled = phoneCfg.pool_enabled !== false;
  const smsbowerEnabled = phoneCfg.smsbower_enabled === true;
  const smspoolEnabled = phoneCfg.smspool_enabled === true;
  return <div className="space-y-6">
    <Card className="rounded-[24px] p-5 sr-sms-provider-card">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2"><h2 className="text-lg font-bold">{t.smsbowerProvider}</h2><Tip text={t.smsbowerDesc}/></div>
        </div>
        <button type="button" aria-label={t.smsbowerSwitch} title={t.smsbowerSwitch} className={cn("sr-switch-only", smsbowerEnabled && "on")} onClick={toggleSMSBowerEnabled}>
          <span />
        </button>
      </div>
      {smsbowerEnabled && <div className="sr-sms-provider-form mt-4 space-y-3">
        <div className="sr-sms-provider-top-row">
          <div className="sr-sms-provider-api"><Label>{t.smsbowerApiKey}</Label><Input type="password" value={phoneCfg.smsbower_api_key||""} onChange={(e)=>setPhoneCfg({...phoneCfg,smsbower_api_key:e.target.value})} placeholder="xxxxxxxxxxxxxxxx"/></div>
          <div><Label>{t.smsbowerCountry}</Label><ProviderOptionSelect className="sr-provider-option-select" value={String(phoneCfg.smsbower_default_country||"187")} onChange={(v)=>setPhoneCfg({...phoneCfg,smsbower_default_country:v})} options={optionsFor("smsbower","countries")} placeholder="187"/></div>
          <div><Label>{t.smsbowerService}</Label><ProviderOptionSelect className="sr-provider-option-select" value={String(phoneCfg.smsbower_default_service||"dr")} onChange={(v)=>setPhoneCfg({...phoneCfg,smsbower_default_service:v})} options={optionsFor("smsbower","services",String(phoneCfg.smsbower_default_country||""))} placeholder="dr"/></div>
          <div className="sr-sms-provider-price"><Label>{t.smsbowerMaxPrice}</Label><Input type="number" value={phoneCfg.smsbower_max_price ?? -1} onChange={(e)=>setPhoneCfg({...phoneCfg,smsbower_max_price:Number(e.target.value)})} placeholder="-1"/></div>
        </div>
        <div className="sr-sms-provider-bottom-row">
        <div><Label>{t.smsbowerBaseURL}</Label><Input value={phoneCfg.smsbower_base_url||"https://smsbower.page/stubs/handler_api.php"} onChange={(e)=>setPhoneCfg({...phoneCfg,smsbower_base_url:e.target.value})}/></div>
        <div className="sr-sms-provider-actions">
          {smsCheck ? <span className="sr-inline-result">{smsCheck}</span> : null}
          <Button variant="outline" className="rounded-xl" onClick={checkSMSBower}><RefreshCw className="mr-2 h-4 w-4"/>{t.smsbowerCheck}</Button>
          <span title={!smsbowerDirty ? t.configUnchanged : ""}>
            <Button disabled={!smsbowerDirty} className="rounded-xl bg-emerald-600 px-5 text-white hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50" onClick={saveSMSBowerConfig}><Save className="mr-2 h-4 w-4"/>{t.save}</Button>
          </span>
        </div>
        </div>
      </div>}
    </Card>
    <Card className="rounded-[24px] p-5 sr-sms-provider-card">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2"><h2 className="text-lg font-bold">{t.smspoolProvider}</h2><Tip text={t.smspoolDesc}/></div>
        </div>
        <button type="button" aria-label={t.smspoolSwitch} title={t.smspoolSwitch} className={cn("sr-switch-only", smspoolEnabled && "on")} onClick={toggleSMSPoolEnabled}>
          <span />
        </button>
      </div>
      {smspoolEnabled && <div className="sr-sms-provider-form mt-4 space-y-3">
        <div className="sr-sms-provider-top-row">
          <div className="sr-sms-provider-api"><Label>{t.smspoolApiKey}</Label><Input type="password" value={phoneCfg.smspool_api_key||""} onChange={(e)=>setPhoneCfg({...phoneCfg,smspool_api_key:e.target.value})} placeholder="xxxxxxxxxxxxxxxx"/></div>
          <div><Label>{t.smspoolCountry}</Label><ProviderOptionSelect className="sr-provider-option-select" value={String(phoneCfg.smspool_default_country||"1")} onChange={(v)=>setPhoneCfg({...phoneCfg,smspool_default_country:v})} options={optionsFor("smspool","countries")} placeholder="1"/></div>
          <div><Label>{t.smspoolService}</Label><ProviderOptionSelect className="sr-provider-option-select" value={String(phoneCfg.smspool_default_service||"OpenAI")} onChange={(v)=>setPhoneCfg({...phoneCfg,smspool_default_service:v})} options={optionsFor("smspool","services",String(phoneCfg.smspool_default_country||""))} placeholder="OpenAI"/></div>
          <div className="sr-sms-provider-price"><Label>{t.smspoolMaxPrice}</Label><Input type="number" value={phoneCfg.smspool_max_price ?? -1} onChange={(e)=>setPhoneCfg({...phoneCfg,smspool_max_price:Number(e.target.value)})} placeholder="-1"/></div>
        </div>
        <div className="sr-sms-provider-bottom-row">
        <div><Label>{t.smspoolBaseURL}</Label><Input value={phoneCfg.smspool_base_url||"https://api.smspool.net"} onChange={(e)=>setPhoneCfg({...phoneCfg,smspool_base_url:e.target.value})}/></div>
        <div className="sr-sms-provider-actions">
          {smsPoolCheck ? <span className="sr-inline-result">{smsPoolCheck}</span> : null}
          <Button variant="outline" className="rounded-xl" onClick={checkSMSPool}><RefreshCw className="mr-2 h-4 w-4"/>{t.smspoolCheck}</Button>
          <span title={!smspoolDirty ? t.configUnchanged : ""}>
            <Button disabled={!smspoolDirty} className="rounded-xl bg-emerald-600 px-5 text-white hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50" onClick={saveSMSPoolConfig}><Save className="mr-2 h-4 w-4"/>{t.save}</Button>
          </span>
        </div>
        </div>
      </div>}
    </Card>
    <Card className="sr-sms-provider-card sr-phone-pool-card rounded-[24px] p-5">
      <div className="flex flex-nowrap items-center justify-between gap-3">
        <div className="flex items-center gap-2"><h2 className="text-lg font-bold">{t.phonePool}</h2><Tip text={t.phonePoolSwitchTip}/></div>
        <button type="button" aria-label={t.phonePoolGlobalSwitch} className={cn("sr-switch-only", poolEnabled && "on")} onClick={togglePoolEnabled} title={t.phonePoolSwitchTip}>
          <span />
        </button>
      </div>
      {poolEnabled ? <div className="sr-phone-expanded mt-4 space-y-4">
        <div className="sr-toolbar sr-toolbar-compact sr-phone-inner-toolbar rounded-[18px] p-4">
          <div className="flex flex-nowrap items-center justify-between gap-3">
          <div className="flex min-w-0 flex-1 flex-nowrap gap-3">
            <div className="relative min-w-[220px] max-w-md flex-1"><Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400"/><input className="sr-search" value={query} onChange={(e)=>setQuery(e.target.value)} placeholder={t.phoneSearch}/></div>
            <SelectBox className="sr-select-like" value={statusFilter} onChange={(v)=>setStatusFilter(String(v))} options={[{value:"",label:t.allStatus}, ...PHONE_STATUS_OPTIONS.map((s)=>({value:s,label:phoneStatusText(t,s)}))]} />
            <SelectBox className="sr-select-like" value={countFilter} onChange={(v)=>setCountFilter(String(v))} options={countOptions} />
          </div>
          <div className="flex flex-nowrap gap-2">
            {selected.length > 0 && <ConfirmBubble message={t.phoneConfirmBatchDelete} detail={`${selected.length} ${t.selected}`} onConfirm={batchDelete}><Button variant="outline" className="rounded-xl border-red-200 text-red-500">{t.batchDelete} ({selected.length})</Button></ConfirmBubble>}
            <button className="sr-text-btn" onClick={()=>run(t.refreshDone, load)}><RefreshCw className="h-4 w-4"/>{t.refresh}</button>
            <Button className="rounded-xl bg-emerald-600 px-4 text-white hover:bg-emerald-700" onClick={()=>setImportOpen(true)}><Download className="mr-2 h-4 w-4"/>{t.importPhones}</Button>
          </div>
          </div>
        </div>
        <div className="sr-table-card overflow-hidden rounded-[18px] p-0">
      <table className="sr-account-table">
        <thead><tr><th><input type="checkbox" checked={allChecked} onChange={(e)=>setSelected(e.target.checked ? items.map((p)=>p.id) : [])}/></th><th>{t.phoneNumber}</th><th>{t.status}</th><th>{t.usedCount}</th><th>{t.smsLink}</th><th><SortTimeHeader label={t.lastUsedAt} order={timeSort} onToggle={()=>setTimeSort(nextSortOrder(timeSort))}/></th><th>{t.actions}</th></tr></thead>
        <tbody>{items.length ? items.map((p)=><tr key={p.id}>
          <td><input type="checkbox" checked={selected.includes(p.id)} onChange={(e)=>setSelected(e.target.checked ? [...selected,p.id] : selected.filter((id)=>id!==p.id))}/></td>
          <td><div className="font-semibold">{p.number}</div>{p.last_error ? <div className="mt-1 max-w-md truncate text-xs text-red-400">{p.last_error}</div> : null}</td>
          <td><span className={cn("sr-status", p.display_status === "disabled" ? "sr-status-gray" : "sr-status-green")}>{phoneStatusText(t, p.display_status || "enabled")}</span></td>
          <td>{p.success_count || 0}/{p.max_success || 3}</td>
          <td><div className="mx-auto max-w-[520px] truncate text-left text-sm text-[var(--text-secondary)]" title={p.sms_url || ""}>{p.sms_url || "-"}</div></td>
          <td>{formatDateTime(p.last_used_at)}</td>
          <td><div className="flex flex-wrap justify-center gap-2"><button className="sr-link" onClick={()=>setEditing(p)}>{t.edit}</button><ConfirmBubble message={t.phoneConfirmDelete} detail={p.number || ""} onConfirm={()=>deletePhone(p)}><button className="sr-link text-red-500">{t.delete}</button></ConfirmBubble></div></td>
        </tr>) : <tr><td colSpan={7}><div className="sr-empty"><div className="sr-empty-icon"><Inbox className="h-7 w-7"/></div><div className="mt-3 text-base font-medium text-slate-900 dark:text-white">{t.noData}</div><p className="mt-2 text-sm text-slate-400">{t.phoneImportHelp}</p></div></td></tr>}</tbody>
      </table>
      <PaginationBar t={t} total={total} page={page} pageSize={pageSize} setPage={setPage} setPageSize={setPageSize} />
        </div>
      </div> : null}
    </Card>
    {importOpen && <PhoneImportModal t={t} onClose={()=>setImportOpen(false)} onImported={()=>{setImportOpen(false); notify("ok", t.done); void load();}} notify={notify}/>}
    {editing && <PhoneEditModal t={t} phone={editing} onClose={()=>setEditing(null)} onSaved={()=>{setEditing(null); notify("ok",t.done); void load();}} notify={notify}/>}
  </div>;
}

function PhoneImportModal({ t, onClose, onImported, notify }: { t: typeof zh; onClose:()=>void; onImported:()=>void; notify:(type:"ok"|"fail", text:string)=>void }) {
  const [mode,setMode]=useState<"file"|"manual">("file");
  const [lines,setLines]=useState("");
  const [drag,setDrag]=useState(false);
  const errors = phoneLineErrors(lines, t.lineFormatPhone);
  const validCount = lines.split(/\r?\n/).filter((x)=>x.trim()).length - errors.length;
  async function pick(file?: File) { if (!file) return; setLines(await file.text()); setMode("file"); }
  async function submit() {
    const trimmed = lines.trim();
    if (!trimmed) { notify("fail", t.phoneImportInvalid); return; }
    if (errors.length) { notify("fail", `${t.phoneImportInvalid}: ${errors[0]}`); return; }
    try {
      const res = await apiFetch("/sunny/phones/import",{method:"POST",body:JSON.stringify({lines:trimmed})});
      if (res.failed > 0) throw new Error((res.errors || []).slice(0, 2).join("\n") || t.phoneImportInvalid);
      onImported();
    } catch(e:any) { notify("fail", e.message || String(e)); }
  }
  return <div className="sr-modal-mask"><div className="sr-modal sr-mailbox-modal">
    <div className="sr-modal-head"><h3>{t.importPhones}</h3><button onClick={onClose}><X className="h-5 w-5"/></button></div>
    <div className="sr-modal-body space-y-5">
      <p className="!mb-0 text-sm text-[var(--text-muted)]">{t.phoneImportHelp}</p>
      <div className="sr-import-tabs"><button className={cn(mode==="file"&&"active")} onClick={()=>setMode("file")}>{t.fileImport}</button><button className={cn(mode==="manual"&&"active")} onClick={()=>setMode("manual")}>{t.manualImport}</button></div>
      {mode==="file" ? <label className={cn("sr-drop-zone", drag && "drag")} onDragOver={(e)=>{e.preventDefault();setDrag(true)}} onDragLeave={()=>setDrag(false)} onDrop={(e)=>{e.preventDefault();setDrag(false);void pick(e.dataTransfer.files?.[0])}}>
        <Download className="h-8 w-8"/><span>{t.dragFile}</span><small>{lines ? `${validCount} valid line(s), ${errors.length} error(s)` : "TXT / CSV"}</small><input type="file" className="hidden" onChange={(e)=>pick(e.target.files?.[0])}/>
      </label> : <Textarea className="min-h-56 rounded-2xl" value={lines} onChange={(e)=>setLines(e.target.value)} placeholder={t.phoneImportPlaceholder}/>} 
      <div className={cn("sr-validation", errors.length ? "bad" : lines.trim() ? "ok" : "")}>{errors.length ? <><b>{t.validationFailed}</b>{errors.slice(0,3).join("锛?)}{errors.length>3?` ... +${errors.length-3}`:""}</> : lines.trim() ? <><b>{t.validationOk}</b>{validCount}</> : t.phoneImportPlaceholder}</div>
    </div>
    <div className="sr-modal-foot"><button onClick={onClose}>{t.cancel}</button><Button className="ml-3 rounded-xl bg-emerald-600 px-6 !text-white hover:bg-emerald-700" disabled={!lines.trim() || errors.length>0} onClick={submit}><Download className="mr-2 h-4 w-4"/>{t.importPhones}</Button></div>
  </div></div>;
}
function PhoneEditModal({ t, phone, onClose, onSaved, notify }: { t: typeof zh; phone: AnyObj; onClose:()=>void; onSaved:()=>void; notify:(type:"ok"|"fail", text:string)=>void }) {
  const [form,setForm]=useState<AnyObj>({...phone, display_status: phone.display_status || (phone.enabled === false ? "disabled" : "enabled")});
  async function save() {
    const number = String(form.number || "").trim();
    const smsURL = String(form.sms_url || "").trim();
    const successCount = Number(form.success_count || 0);
    if (!number.startsWith("+") || !smsURL.toLowerCase().startsWith("http") || Number.isNaN(successCount) || successCount < 0 || successCount > 3) {
      notify("fail", t.validationFailed);
      return;
    }
    try {
      const status = String(form.display_status || "enabled");
      await apiFetch(`/sunny/phones/${phone.id}`, { method:"PUT", body: JSON.stringify({
        number, sms_url: smsURL, status, enabled: status !== "disabled", success_count: successCount, max_success: 3,
      })});
      onSaved();
    } catch(e:any) { notify("fail", e.message || String(e)); }
  }
  return <div className="sr-modal-mask"><div className="sr-modal sr-mailbox-modal">
    <div className="sr-modal-head"><h3>{t.phoneEdit}</h3><button onClick={onClose}><X className="h-5 w-5"/></button></div>
    <div className="sr-modal-body space-y-4">
      <div className="grid gap-4 md:grid-cols-2">
        <div><Label>{t.phoneNumber}</Label><Input value={form.number||""} onChange={(e)=>setForm({...form,number:e.target.value})}/></div>
        <div><Label>{t.status}</Label><SelectBox value={form.display_status||"enabled"} onChange={(v)=>setForm({...form,display_status:String(v)})} options={PHONE_STATUS_OPTIONS.map((s)=>({value:s,label:phoneStatusText(t,s)}))} /></div>
        <div><Label>{t.usedCount}</Label><Input type="number" min={0} max={3} value={form.success_count ?? 0} onChange={(e)=>setForm({...form,success_count:Number(e.target.value)})}/></div>
        <div className="md:col-span-2"><Label>{t.smsLink}</Label><Textarea className="min-h-[92px]" value={form.sms_url||""} onChange={(e)=>setForm({...form,sms_url:e.target.value})}/></div>
      </div>
    </div>
    <div className="sr-modal-foot"><button onClick={onClose}>{t.cancel}</button><Button className="ml-3 rounded-xl bg-emerald-600 px-6 !text-white hover:bg-emerald-700" onClick={save}><Save className="mr-2 h-4 w-4"/>{t.save}</Button></div>
  </div></div>;
}
function normalizeSub2APIGroups(resp: AnyObj): AnyObj[] {
  const pick = (v: any): any[] => Array.isArray(v) ? v : [];
  const candidates = [pick(resp), pick(resp.items), pick(resp.data), pick(resp.groups), pick(resp.result)];
  const data = candidates.find((x)=>x.length) || [];
  const nestedCandidates = resp.data && typeof resp.data === "object" ? [pick(resp.data.items), pick(resp.data.groups), pick(resp.data.list)] : [];
  const nested = nestedCandidates.find((x)=>x.length) || [];
  return (data.length ? data : nested).map((g: AnyObj) => ({
    id: String(g.id ?? g.group_id ?? g.value ?? g.key ?? ""),
    name: String(g.name ?? g.label ?? g.group_name ?? g.display_name ?? g.id ?? ""),
  })).filter((g: AnyObj) => g.id);
}

function normalizeSub2APIConfig(cfg: AnyObj) {
  const groupIds = Array.isArray(cfg.group_ids) ? cfg.group_ids.map((x:any)=>String(x)).filter(Boolean) : String(cfg.group_ids||"").split(",").map((x)=>x.trim()).filter(Boolean);
  const rawLabels = cfg.group_labels && typeof cfg.group_labels === "object" ? cfg.group_labels : {};
  const groupLabels = Object.fromEntries(groupIds.map((id)=>[id, String(rawLabels[id] || "").trim()]).filter(([,name])=>name));
  return {
    enabled: cfg.enabled !== false,
    base_url: String(cfg.base_url || "").trim(),
    admin_token: String(cfg.admin_token || "").trim(),
    name_prefix: String(cfg.name_prefix || ""),
    group_ids: groupIds,
    group_labels: groupLabels,
    concurrency: Number(cfg.concurrency || 3),
    priority: Number(cfg.priority || 50),
    codex_image_bridge: false,
  };
}

function sub2apiGroupLabel(group: AnyObj) {
  const id = String(group.id ?? "").trim();
  const name = String(group.name ?? "").trim();
  return name && name !== id ? `${id}路${name}` : id;
}

function Sub2APIConfig({ t, notify }: { t: typeof zh; notify: (type: "ok" | "fail", text: string) => void }) {
  const [cfg,setCfg]=useCachedState<AnyObj>("sub2api.cfg",{});
  const [savedCfg,setSavedCfg]=useCachedState<AnyObj>("sub2api.savedCfg",{});
  const [groups,setGroups]=useCachedState<AnyObj[]>("sub2api.groups",[]);
  const [loading,setLoading]=useCachedState("sub2api.loading",false);
  const [fetching,setFetching]=useCachedState("sub2api.fetchingGroups",false);
  const [checkStatus,setCheckStatus]=useCachedState<AnyObj|null>("sub2api.checkStatus",null);
  const [groupOpen,setGroupOpen]=useState(false);
  const selectedGroupIds = Array.isArray(cfg.group_ids) ? cfg.group_ids.map((x:any)=>String(x)) : String(cfg.group_ids||"").split(",").map((x)=>x.trim()).filter(Boolean);
  const savedGroupLabels = cfg.group_labels && typeof cfg.group_labels === "object" ? cfg.group_labels : {};
  const labelForGroupId = (id: string) => {
    const group = groups.find((g)=>String(g.id) === String(id));
    if (group) return sub2apiGroupLabel(group);
    const saved = String(savedGroupLabels[id] || "").trim();
    return saved ? `${id}路${saved}` : id;
  };
  const buildGroupLabels = (ids: string[]) => Object.fromEntries(ids.map((id)=>{
    const group = groups.find((g)=>String(g.id) === String(id));
    const name = String(group?.name || savedGroupLabels[id] || "").trim();
    return [id, name && name !== id ? name : ""];
  }).filter(([,name])=>name));
  const sub2apiEnabled = cfg.enabled !== false;
  const dirty = JSON.stringify(normalizeSub2APIConfig(cfg)) !== JSON.stringify(normalizeSub2APIConfig(savedCfg));
  const setGroupIds = (ids: string[]) => setCfg({...cfg, group_ids: ids, group_labels: buildGroupLabels(ids)});
  const toggleGroup = (id: string) => setGroupIds(selectedGroupIds.includes(id) ? selectedGroupIds.filter((x)=>x!==id) : [...selectedGroupIds, id]);
  async function fetchGroups(silent=false, sourceCfg: AnyObj = cfg){
    if (!String(sourceCfg.base_url||"").trim() || !String(sourceCfg.admin_token||"").trim()) {
      if (!silent) notify("fail", t.fillURLToken);
      return;
    }
    setFetching(true);
    try {
      const qs = new URLSearchParams({base_url:String(sourceCfg.base_url||""), admin_token:String(sourceCfg.admin_token||"")});
      const resp = await apiFetch(`/sunny/sub2api/groups?${qs.toString()}`);
      const list = normalizeSub2APIGroups(resp);
      setGroups(list);
      if (selectedGroupIds.length) {
        const nextLabels = Object.fromEntries(selectedGroupIds.map((id)=>{
          const group = list.find((g)=>String(g.id) === String(id));
          const name = String(group?.name || savedGroupLabels[id] || "").trim();
          return [id, name && name !== id ? name : ""];
        }).filter(([,name])=>name));
        setCfg({...cfg, group_labels: nextLabels});
      }
      if (!silent) notify("ok", template(t.fetchedGroups, { count: list.length }));
      if (list.length) setGroupOpen(true);
    } catch(e:any) {
      if (!silent) notify("fail", e.message || String(e));
    } finally {
      setFetching(false);
    }
  }
  async function checkConnection(){
    if (!String(cfg.base_url||"").trim() || !String(cfg.admin_token||"").trim()) {
      setCheckStatus({type:"fail", text:t.fillURLTokenShort});
      return;
    }
    setCheckStatus({type:"loading", text:t.checking});
    setFetching(true);
    try {
      const qs = new URLSearchParams({base_url:String(cfg.base_url||""), admin_token:String(cfg.admin_token||"")});
      const resp = await apiFetch(`/sunny/sub2api/groups?${qs.toString()}`);
      const list = normalizeSub2APIGroups(resp);
      setGroups(list);
      if (selectedGroupIds.length) {
        const nextLabels = Object.fromEntries(selectedGroupIds.map((id)=>{
          const group = list.find((g)=>String(g.id) === String(id));
          const name = String(group?.name || savedGroupLabels[id] || "").trim();
          return [id, name && name !== id ? name : ""];
        }).filter(([,name])=>name));
        setCfg({...cfg, group_labels: nextLabels});
      }
      setCheckStatus({type:"ok", text:template(t.checkPassedGroups, { count: list.length })});
    } catch(e:any) {
      setCheckStatus({type:"fail", text:template(t.checkFailed, { error: e.message || String(e) })});
    } finally {
      setFetching(false);
    }
  }
  useEffect(()=>{
    let alive = true;
    apiFetch("/sunny/sub2api-config").then((data)=>{
      if (!alive) return;
      setCfg(data);
      setSavedCfg(data);
      const next = normalizeSub2APIConfig(data);
      const base = String(next.base_url || "").trim();
      const token = String(next.admin_token || "").trim();
      if (next.enabled !== false && next.group_ids.length === 0 && base && token.length >= 8) {
        window.setTimeout(()=>{ if (alive) void fetchGroups(true, next); }, 300);
      }
    }).catch((e)=>notify("fail", e.message||String(e)));
    return ()=>{ alive = false; };
  },[]);
  async function toggleSub2APIEnabled(){
    const nextEnabled = !sub2apiEnabled;
    const next = normalizeSub2APIConfig({...cfg, enabled: nextEnabled, group_labels: buildGroupLabels(selectedGroupIds)});
    setCfg(next);
    setLoading(true);
    try{
      await apiFetch("/sunny/sub2api-config",{method:"PUT",body:JSON.stringify(next)});
      setSavedCfg(next);
      notify("ok",t.done);
    }
    catch(e:any){notify("fail",e.message||String(e))}
    finally{setLoading(false)}
  }
  async function save(){
    if (!dirty) return;
    setLoading(true);
    try{
      const next = normalizeSub2APIConfig({...cfg, group_labels: buildGroupLabels(selectedGroupIds)});
      await apiFetch("/sunny/sub2api-config",{method:"PUT",body:JSON.stringify(next)});
      setCfg(next);
      setSavedCfg(next);
      notify("ok",t.done)
    }
    catch(e:any){notify("fail",e.message||String(e))}
    finally{setLoading(false)}
  }
  return <Card className="rounded-[24px] p-5 sr-sms-provider-card">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div>
        <div className="flex items-center gap-2"><h2 className="text-lg font-bold">sub2api</h2><Tip text={t.sub2apiDesc}/></div>
      </div>
      <button type="button" aria-label="sub2api" title="sub2api" className={cn("sr-switch-only", sub2apiEnabled && "on")} onClick={toggleSub2APIEnabled} disabled={loading}>
        <span />
      </button>
    </div>
    {sub2apiEnabled && <div className="mt-4 grid gap-4 md:grid-cols-2">
      <div><Label>{t.baseURL}</Label><Input placeholder="https://your-sub2api.example.com" value={cfg.base_url||""} onChange={(e)=>setCfg({...cfg,base_url:e.target.value})}/></div>
      <div><Label>{t.adminToken}</Label><Input type="password" placeholder="x-api-key" value={cfg.admin_token||""} onChange={(e)=>setCfg({...cfg,admin_token:e.target.value})}/></div>
      <div><Label>{t.accountNamePrefix}</Label><Input placeholder="Sunny-" value={cfg.name_prefix||""} onChange={(e)=>setCfg({...cfg,name_prefix:e.target.value})}/></div>
      <div>
        <Label>{t.targetGroup}</Label>
        <div className="sr-group-picker-row">
          <div className="sr-group-picker" tabIndex={0} onBlur={() => window.setTimeout(()=>setGroupOpen(false), 120)}>
            <button type="button" className={cn("sr-group-picker-trigger", groupOpen && "open")} onClick={()=>setGroupOpen((v)=>!v)}>
              <span className={cn("sr-group-picker-placeholder", selectedGroupIds.length && "has-value")}>
                {selectedGroupIds.length ? selectedGroupIds.map(labelForGroupId).join(", ") : t.targetGroupPlaceholder}
              </span>
              <ChevronDown className={cn("h-4 w-4 transition-transform", groupOpen && "rotate-180")} />
            </button>
            {groupOpen && <div className="sr-group-picker-menu">
              {groups.length ? groups.map((g)=>{
                const checked = selectedGroupIds.includes(String(g.id));
                return <button type="button" key={String(g.id)} className={cn("sr-group-picker-option", checked && "selected")} onMouseDown={(e)=>e.preventDefault()} onClick={()=>toggleGroup(String(g.id))}>
                  <span className={cn("sr-group-check", checked && "on")}>{checked ? "鉁? : ""}</span>
                  <span className="sr-group-name">{sub2apiGroupLabel(g)}</span>
                  <span className="sr-group-id">ID {g.id}</span>
                </button>
              }) : <div className="sr-group-empty">{t.noGroupsFetch}</div>}
            </div>}
          </div>
          <Button variant="outline" className="h-11 rounded-xl" disabled={fetching} onClick={()=>fetchGroups(false)}>{fetching?<Loader2 className="mr-2 h-4 w-4 animate-spin"/>:<RefreshCw className="mr-2 h-4 w-4"/>}{t.fetch}</Button>
        </div>
      </div>
      <div><Label>{t.concurrency}</Label><Input type="number" value={cfg.concurrency||3} onChange={(e)=>setCfg({...cfg,concurrency:Number(e.target.value||3)})}/></div>
      <div><Label>{t.priority}</Label><Input type="number" value={cfg.priority||50} onChange={(e)=>setCfg({...cfg,priority:Number(e.target.value||50)})}/></div>
    </div>}
    {sub2apiEnabled && <div className="mt-5 flex items-center justify-end gap-3">
      {checkStatus ? <span className={cn("sr-check-status", checkStatus.type === "ok" && "ok", checkStatus.type === "fail" && "fail")}>{checkStatus.text}</span> : null}
      <Button variant="outline" className="rounded-xl px-5" disabled={fetching || loading} onClick={checkConnection}>{fetching?<Loader2 className="mr-2 h-4 w-4 animate-spin"/>:<RefreshCw className="mr-2 h-4 w-4"/>}{t.check}</Button>
      <span title={!dirty ? t.configUnchanged : ""}>
        <Button className="rounded-xl bg-emerald-600 px-6 text-white hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50" disabled={loading || !dirty} onClick={save}>{loading?<Loader2 className="mr-2 h-4 w-4 animate-spin"/>:<Save className="mr-2 h-4 w-4"/>}{t.save}</Button>
      </span>
    </div>}
  </Card>;
}
const PROXY_STATUSES = ["鍚敤", "鍋滅敤", "澶辨晥"];

function ProxyStatusBadge({ t, status }: { t: typeof zh; status: string }) {
  const normalized = status === "鍙敤" ? "鍚敤" : status;
  const map: Record<string,string> = { "鍚敤": "green", "鍋滅敤": "gray", "澶辨晥": "red" };
  const labelMap: Record<string,string> = { "鍚敤": t.proxyStatusEnabled, "鍋滅敤": t.proxyStatusDisabled, "澶辨晥": t.proxyStatusInvalid };
  return <span className={cn("sr-status", `sr-status-${map[normalized] || "gray"}`)}>{labelMap[normalized] || normalized}</span>;
}

function ProxyConfigPage({ t, notify }: { t: typeof zh; notify: (type: "ok" | "fail", text: string) => void }) {
  const [items,setItems]=useCachedState<AnyObj[]>("proxy.items",[]);
  const [stats,setStats]=useCachedState<AnyObj>("proxy.stats",{total:0,enabled:0,available:0});
  const [countries,setCountries]=useCachedState<string[]>("proxy.countries",[]);
  const [query,setQuery]=useCachedState("proxy.query","");
  const [status,setStatus]=useCachedState("proxy.status","");
  const [country,setCountry]=useCachedState("proxy.country","");
  const [timeSort,setTimeSort]=useCachedState<SortOrder>("proxy.timeSort","desc");
  const [page,setPage]=useCachedState("proxy.page",1);
  const [pageSize,setPageSize]=useCachedState("proxy.pageSize",10);
  const [total,setTotal]=useCachedState("proxy.total",0);
  const [loading,setLoading]=useCachedState("proxy.loading",false);
  const [editing,setEditing]=useCachedState<AnyObj|null>("proxy.editing",null);
  const [selected,setSelected]=useCachedState<number[]>("proxy.selected",[]);
  const [batchEditing,setBatchEditing]=useCachedState<AnyObj|null>("proxy.batchEditing",null);
  const [proxyCfg,setProxyCfg]=useCachedState<AnyObj>("proxy.cfg",{proxy_enabled:true});
  const [proxySaving,setProxySaving]=useCachedState("proxy.savingCfg",false);
  const load = async () => {
    const qs = new URLSearchParams({page:String(page), page_size:String(pageSize), q:query, status, country, sort_by:"last_checked_at", sort_order:timeSort});
    const res = await apiFetch(`/sunny/proxy-config/pool?${qs.toString()}`);
    setItems(res.items || []);
    setStats(res.stats || {total:0,enabled:0,available:0});
    setCountries(res.countries || []);
    setTotal(Number(res.total || 0));
  };
  const loadConfig = async () => {
    const cfg = await apiFetch("/sunny/proxy-config");
    setProxyCfg(cfg || {proxy_enabled:true});
  };
  useEffect(()=>{void load().catch((e:any)=>notify("fail", e.message || String(e)))},[page, pageSize, query, status, country, timeSort]);
  useEffect(()=>{void loadConfig().catch((e:any)=>notify("fail", e.message || String(e)))},[]);
  useEffect(()=>{setPage(1)},[query, status, country, timeSort, pageSize]);
  useEffect(()=>{const pages=pageCount(total,pageSize); if(page>pages) setPage(pages);},[total,pageSize,page]);
  const trafficProxyEnabled = proxyCfg.proxy_enabled !== false;
  async function toggleTrafficProxy(){
    setProxySaving(true);
    try {
      const next = {...proxyCfg, proxy_enabled: !trafficProxyEnabled};
      const saved = await apiFetch("/sunny/proxy-config", {method:"PUT", body: JSON.stringify(next)});
      setProxyCfg(saved || next);
      notify("ok", t.proxySwitchSaved);
    } catch(e:any) { notify("fail", e.message || String(e)); }
    finally { setProxySaving(false); }
  }
  async function batchCheck(){
    setLoading(true);
    try {
      const ids = selected.length ? selected : items.map((x)=>Number(x.id)).filter(Boolean);
      const res = await apiFetch("/sunny/proxy-config/pool/check", { method:"POST", body: JSON.stringify({ids}) });
      notify("ok", `${t.proxyCheckDone}: ${res.available || 0}/${res.checked || 0}`);
      await load();
    } catch(e:any) { notify("fail", e.message || String(e)); }
    finally { setLoading(false); }
  }
  async function checkOne(row: AnyObj){
    setLoading(true);
    try {
      await apiFetch(`/sunny/proxy-config/pool/${row.id}/check`, { method:"POST" });
      notify("ok", t.proxyCheckDone);
      await load();
    } catch(e:any) { notify("fail", e.message || String(e)); }
    finally { setLoading(false); }
  }
  async function deleteProxy(row: AnyObj){
    try {
      await apiFetch(`/sunny/proxy-config/pool/${row.id}`, { method:"DELETE" });
      notify("ok", t.done);
      await load();
    } catch(e:any) { notify("fail", e.message || String(e)); }
  }
  async function batchDeleteProxy(){
    if (!selected.length) return;
    try {
      await Promise.all(selected.map((id)=>apiFetch(`/sunny/proxy-config/pool/${id}`, { method:"DELETE" })));
      setSelected([]);
      notify("ok", t.done);
      await load();
    } catch(e:any) { notify("fail", e.message || String(e)); }
  }
  async function batchUpdateProxy(form: AnyObj){
    if (!selected.length) return;
    const statusValue = String(form.status || "鍚敤");
    try {
      await Promise.all(selected.map((id)=>apiFetch(`/sunny/proxy-config/pool/${id}`, { method:"PUT", body: JSON.stringify({country: form.country, status: statusValue, enabled: statusValue === "鍚敤"}) })));
      setBatchEditing(null);
      notify("ok", t.done);
      await load();
    } catch(e:any) { notify("fail", e.message || String(e)); }
  }
  const countryOptions = [{value:"",label:t.proxyAllCountry}, ...countries.map((c)=>({value:c,label:c}))];
  const allChecked = items.length > 0 && items.every((p)=>selected.includes(Number(p.id)));
  const statusOptions = PROXY_STATUSES.map((s)=>({value:s,label:s==="鍚敤"?t.proxyStatusEnabled:s==="鍋滅敤"?t.proxyStatusDisabled:t.proxyStatusInvalid}));
  async function refreshProxyList(){
    try { await load(); notify("ok", t.refreshDone); }
    catch(e:any) { notify("fail", e.message || String(e)); }
  }
  return <div className="space-y-4">
    <Card className="rounded-[26px] p-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Label tip={t.proxyTip}>{t.proxy}</Label>
          <p className="text-sm leading-6 text-[var(--text-muted)]">{t.proxyTip}</p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <button type="button" className={cn("sr-setting-switch", trafficProxyEnabled ? "on" : "off")} onClick={toggleTrafficProxy} disabled={proxySaving} title={t.proxyTrafficSwitch}>
            <span className="sr-setting-switch-knob" />
            <span className="sr-setting-switch-text">
              <b>{trafficProxyEnabled ? t.proxyTrafficOn : t.proxyTrafficOff}</b>
              <small>{trafficProxyEnabled ? t.proxyTrafficOnHint : t.proxyTrafficOffHint}</small>
            </span>
            {proxySaving ? <Loader2 className="ml-1 h-4 w-4 animate-spin opacity-70"/> : null}
          </button>
          <Button className="rounded-xl bg-emerald-600 px-4 text-white hover:bg-emerald-700" onClick={()=>setEditing({address:"",country:"",status:"鍚敤",enabled:true})}><Plus className="mr-2 h-4 w-4"/>{t.proxyAdd}</Button>
        </div>
      </div>
      <div className="mt-5 grid gap-3 md:grid-cols-4">
        <div className="sr-proxy-stat"><span>{t.proxyPool}</span><b>{stats.total || 0}</b></div>
        <div className="sr-proxy-stat"><span>{t.proxyEnabled}</span><b>{stats.enabled || 0}</b></div>
        <div className="sr-proxy-stat"><span>{t.proxyStatusDisabled}</span><b>{stats.disabled || 0}</b></div>
        <div className="sr-proxy-stat"><span>{t.proxyStatusInvalid}</span><b>{stats.invalid || 0}</b></div>
      </div>
    </Card>
    <Card className="sr-toolbar rounded-[18px] p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 flex-1 flex-wrap gap-3">
          <div className="relative min-w-[260px] max-w-lg flex-1"><Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400"/><input className="sr-search" value={query} onChange={(e)=>setQuery(e.target.value)} placeholder={t.proxySearch}/></div>
          <SelectBox className="sr-select-like" value={status} onChange={(v)=>setStatus(String(v))} options={[{value:"",label:t.allStatus}, ...statusOptions]} />
          <SelectBox className="sr-select-like" value={country} onChange={(v)=>setCountry(String(v))} options={countryOptions} />
        </div>
        <div className="flex flex-wrap gap-2">
          {selected.length > 0 && <Button variant="outline" className="rounded-xl" onClick={()=>setBatchEditing({country:"",status:"鍚敤"})}>{t.proxyBatchEdit} ({selected.length})</Button>}
          {selected.length > 0 && <ConfirmBubble message={t.proxyConfirmBatchDelete} detail={`${selected.length} ${t.selected}`} onConfirm={batchDeleteProxy}><Button variant="outline" className="rounded-xl border-red-200 text-red-500">{t.proxyBatchDelete} ({selected.length})</Button></ConfirmBubble>}
          <button className="sr-text-btn" onClick={refreshProxyList}><RefreshCw className="h-4 w-4"/>{t.refresh}</button>
          <Button variant="outline" className="rounded-xl" disabled={loading || !items.length} onClick={batchCheck}>{loading?<Loader2 className="mr-2 h-4 w-4 animate-spin"/>:<Settings2 className="mr-2 h-4 w-4"/>}{t.proxyBatchCheck}</Button>
        </div>
      </div>
    </Card>
    <Card className="sr-table-card overflow-hidden rounded-[18px] p-0">
      <table className="sr-account-table sr-proxy-table">
        <thead><tr><th><input type="checkbox" checked={allChecked} onChange={(e)=>setSelected(e.target.checked ? items.map((p)=>Number(p.id)) : [])}/></th><th>{t.proxyAddress}</th><th>{t.proxyCountry}</th><th>{t.status}</th><th><SortTimeHeader label={t.proxyLastChecked} order={timeSort} onToggle={()=>setTimeSort(nextSortOrder(timeSort))}/></th><th>{t.operation}</th></tr></thead>
        <tbody>{items.length ? items.map((p)=><tr key={p.id}>
          <td><input type="checkbox" checked={selected.includes(Number(p.id))} onChange={(e)=>setSelected(e.target.checked ? [...selected, Number(p.id)] : selected.filter((id)=>id!==Number(p.id)))}/></td>
          <td><div className="font-semibold">{p.address}</div>{p.last_error ? <div className="mt-1 max-w-xl truncate text-xs text-red-400">{p.last_error}</div> : null}</td>
          <td>{p.country || "-"}</td>
          <td><ProxyStatusBadge t={t} status={p.status || "鍚敤"} />{p.latency_ms ? <div className="mt-1 text-xs text-[var(--text-muted)]">{t.proxyLatency}: {p.latency_ms}ms</div> : null}</td>
          <td>{formatDateTime(p.last_checked_at)}</td>
          <td><div className="flex flex-wrap justify-center gap-2"><button className="sr-link" disabled={loading} onClick={()=>checkOne(p)}>{t.refresh}</button><button className="sr-link" onClick={()=>setEditing(p)}>{t.edit}</button><ConfirmBubble message={t.proxyConfirmDelete} detail={p.address || ""} onConfirm={()=>deleteProxy(p)}><button className="sr-link text-red-500">{t.delete}</button></ConfirmBubble></div></td>
        </tr>) : <tr><td colSpan={6}><div className="sr-empty"><div className="sr-empty-icon"><Settings2 className="h-7 w-7"/></div><div className="mt-3 text-base font-medium text-slate-900 dark:text-white">{t.proxyNoData}</div><p className="mt-2 text-sm text-slate-400">{t.proxyNoDataDesc}</p></div></td></tr>}</tbody>
      </table>
      <PaginationBar t={t} total={total} page={page} pageSize={pageSize} setPage={setPage} setPageSize={setPageSize} />
    </Card>
    {editing && <ProxyEditModal t={t} proxy={editing} onClose={()=>setEditing(null)} onSaved={()=>{setEditing(null); notify("ok", t.done); void load();}} notify={notify}/>}
    {batchEditing && <ProxyBatchEditModal t={t} count={selected.length} form={batchEditing} setForm={setBatchEditing} onClose={()=>setBatchEditing(null)} onSaved={()=>batchUpdateProxy(batchEditing)} />}
  </div>;
}

function ProxyEditModal({ t, proxy, onClose, onSaved, notify }: { t: typeof zh; proxy: AnyObj; onClose:()=>void; onSaved:()=>void; notify:(type:"ok"|"fail", text:string)=>void }) {
  const [form,setForm]=useState<AnyObj>({...proxy, status: proxy.status || "鍚敤"});
  const isNew = !form.id;
  async function save(){
    const lines = String(form.address || "").split(/\r?\n/).map((x)=>x.trim()).filter(Boolean);
    if (!lines.length) { notify("fail", t.validationFailed); return; }
    const status = String(form.status || "鍚敤");
    try {
      await apiFetch(isNew ? "/sunny/proxy-config/pool" : `/sunny/proxy-config/pool/${form.id}`, {
        method: isNew ? "POST" : "PUT",
        body: JSON.stringify(isNew ? {addresses:lines, country:form.country, status, enabled: status !== "鍋滅敤"} : {address:lines[0], country:form.country, status, enabled: status !== "鍋滅敤"}),
      });
      onSaved();
    } catch(e:any) { notify("fail", e.message || String(e)); }
  }
  return <div className="sr-modal-mask"><div className="sr-modal sr-mailbox-modal">
    <div className="sr-modal-head"><h3>{isNew ? t.proxyAdd : t.proxyEdit}</h3><button onClick={onClose}><X className="h-5 w-5"/></button></div>
    <div className="sr-modal-body space-y-4">
      <div><Label>{t.proxyAddress}</Label>{isNew ? <Textarea className="min-h-40 rounded-[14px]" placeholder={t.proxyAddressPlaceholder} value={form.address||""} onChange={(e)=>setForm({...form,address:e.target.value})}/> : <Input placeholder={t.proxyAddressPlaceholder} value={form.address||""} onChange={(e)=>setForm({...form,address:e.target.value})}/>}</div>
      <div className="grid gap-4 md:grid-cols-2">
        <div><Label>{t.proxyCountry}</Label><Input placeholder={t.proxyCountryPlaceholder} value={form.country||""} onChange={(e)=>setForm({...form,country:e.target.value})}/></div>
        <div><Label>{t.status}</Label><SelectBox value={form.status||"鍚敤"} onChange={(v)=>setForm({...form,status:String(v)})} options={PROXY_STATUSES.map((s)=>({value:s,label:s==="鍚敤"?t.proxyStatusEnabled:s==="鍋滅敤"?t.proxyStatusDisabled:t.proxyStatusInvalid}))} /></div>
      </div>
    </div>
    <div className="sr-modal-foot"><button onClick={onClose}>{t.cancel}</button><Button className="ml-3 rounded-xl bg-emerald-600 px-6 !text-white hover:bg-emerald-700" onClick={save}><Save className="mr-2 h-4 w-4"/>{t.save}</Button></div>
  </div></div>;
}

function ProxyBatchEditModal({ t, count, form, setForm, onClose, onSaved }: { t: typeof zh; count: number; form: AnyObj; setForm:(v:AnyObj)=>void; onClose:()=>void; onSaved:()=>void }) {
  return <div className="sr-modal-mask"><div className="sr-modal sr-mailbox-modal">
    <div className="sr-modal-head"><h3>{t.proxyBatchEdit}</h3><button onClick={onClose}><X className="h-5 w-5"/></button></div>
    <div className="sr-modal-body space-y-4">
      <div className="rounded-2xl border border-emerald-200 bg-emerald-50/70 px-4 py-3 text-sm font-bold text-emerald-700 dark:border-emerald-400/20 dark:bg-emerald-400/10 dark:text-emerald-200">{t.selected}: {count}</div>
      <div className="grid gap-4 md:grid-cols-2">
        <div><Label>{t.proxyCountry}</Label><Input placeholder={t.proxyCountryPlaceholder} value={form.country||""} onChange={(e)=>setForm({...form,country:e.target.value})}/></div>
        <div><Label>{t.status}</Label><SelectBox value={form.status||"鍚敤"} onChange={(v)=>setForm({...form,status:String(v)})} options={PROXY_STATUSES.map((s)=>({value:s,label:s==="鍚敤"?t.proxyStatusEnabled:s==="鍋滅敤"?t.proxyStatusDisabled:t.proxyStatusInvalid}))} /></div>
      </div>
    </div>
    <div className="sr-modal-foot"><button onClick={onClose}>{t.cancel}</button><Button className="ml-3 rounded-xl bg-emerald-600 px-6 !text-white hover:bg-emerald-700" onClick={onSaved}><Save className="mr-2 h-4 w-4"/>{t.save}</Button></div>
  </div></div>;
}
function tokenPreview(value: any) {
  const text = String(value || "").trim();
  if (!text) return "-";
  return text.length > 24 ? `${text.slice(0, 20)}...` : text;
}
function tokenTailPreview(value: any) {
  const text = String(value || "").trim();
  if (!text) return "-";
  return text.length > 24 ? `...${text.slice(-20)}` : text;
}

const SESSION_PLAN_OPTIONS = PLAN_TYPE_OPTIONS;
const SESSION_STATUS_OPTIONS = [...MAILBOX_STATUSES, "澶辫触"];
function SessionManager({ t, notify }: { t: typeof zh; notify: (type: "ok" | "fail", text: string) => void }) {
  const [items,setItems]=useCachedState<AnyObj[]>("session.items",[]);
  const [fmt,setFmt]=useCachedState("session.fmt","mailbox_account");
  const [query,setQuery]=useCachedState("session.query","");
  const [status,setStatus]=useCachedState("session.status","");
  const [plan,setPlan]=useCachedState("session.plan","");
  const [selected,setSelected]=useCachedState<number[]>("session.selected",[]);
  const [editing,setEditing]=useCachedState<AnyObj|null>("session.editing",null);
  const [timeSort]=useCachedState<SortOrder>("session.timeSort","desc");
  const [page,setPage]=useCachedState("session.page",1);
  const [pageSize,setPageSize]=useCachedState("session.pageSize",10);
  const [total,setTotal]=useCachedState("session.total",0);
  const load=async()=>{
    const qs = new URLSearchParams({ page:String(page), page_size:String(pageSize), sort_by:"updated_at", sort_order:timeSort });
    if (query.trim()) qs.set("q", query.trim());
    if (status) qs.set("status", status);
    if (plan) qs.set("plan_type", plan);
    const res = await apiFetch(`/sunny/sessions?${qs.toString()}`);
    setItems(res.items||[]);
    setTotal(Number(res.total || 0));
  };
  useEffect(()=>{void load()},[timeSort, page, pageSize, query, status, plan]);
  useEffect(()=>{setPage(1)},[timeSort, pageSize, query, status, plan]);
  useEffect(()=>{const pages=pageCount(total,pageSize); if(page>pages) setPage(pages);},[total,pageSize,page]);
  const allChecked = items.length > 0 && items.every((x)=>selected.includes(x.id));
  async function exp(ids?: number[], format = fmt){
    const sessionIds = ids?.length ? ids : selected;
    if (!sessionIds.length) { notify("fail", t.selectExportRows); return; }
    try{const {blob,filename}=await apiDownload("/sunny/sessions/export",{method:"POST",body:JSON.stringify({format, session_ids: sessionIds})});triggerBrowserDownload(blob,filename);notify("ok",t.done)}
    catch(e:any){notify("fail",e.message||String(e))}
  }
  async function del(row: AnyObj) {
    try { await apiFetch(`/sunny/sessions/${row.id}`, { method:"DELETE" }); notify("ok", t.done); setSelected((old)=>old.filter((id)=>id!==row.id)); void load(); }
    catch(e:any){ notify("fail", e.message || String(e)); }
  }
  async function refreshSessionList() {
    try { await load(); notify("ok", t.refreshDone); }
    catch(e:any){ notify("fail", e.message || String(e)); }
  }
  return <Card className="rounded-[30px] p-5">
    <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
      <h2 className="text-xl font-bold">{t.session}</h2>
      <div className="flex flex-wrap items-center gap-2">
        <SelectBox className="sr-select-like" value={fmt} onChange={(v)=>setFmt(String(v))} options={[{value:"mailbox_account",label:t.mailboxAccountExport},{value:"session_json",label:t.sessionJSON},{value:"access_token",label:t.accessToken}]} />
        <Button className="rounded-full" onClick={()=>exp()}><Download className="mr-2 h-4 w-4"/>{t.export}</Button>
      </div>
    </div>
    <div className="mb-4 flex flex-wrap items-center gap-3">
      <div className="relative min-w-[260px] max-w-lg flex-1"><Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400"/><input className="sr-search" value={query} onChange={(e)=>setQuery(e.target.value)} placeholder={t.searchAccount} /></div>
      <SelectBox className="sr-select-like" value={status} onChange={(v)=>setStatus(String(v))} options={[{value:"",label:t.allStatus}, ...SESSION_STATUS_OPTIONS.map((s)=>({value:s,label:t.statusLabels[s as keyof typeof t.statusLabels] || s}))]} />
      <SelectBox className="sr-select-like" value={plan} onChange={(v)=>setPlan(String(v))} options={[{value:"",label:t.planType}, ...SESSION_PLAN_OPTIONS.map((p)=>({value:p,label:formatPlanType(p)}))]} />
      <button className="sr-text-btn" onClick={refreshSessionList}><RefreshCw className="h-4 w-4"/>{t.refresh}</button>
    </div>
    <table className="sr-account-table"><thead><tr><th><input type="checkbox" checked={allChecked} onChange={(e)=>setSelected(e.target.checked ? Array.from(new Set([...selected, ...items.map((x)=>x.id)])) : selected.filter((id)=>!items.some((x)=>x.id===id)))}/></th><th>{t.email}</th><th>{t.status}</th><th>{t.planType}</th><th>{t.accessToken}</th><th>{t.sessionRefreshToken}</th><th>{t.operation}</th></tr></thead><tbody>{items.length ? items.map((s)=><tr key={s.id}><td><input type="checkbox" checked={selected.includes(s.id)} onChange={(e)=>setSelected(e.target.checked ? [...selected, s.id] : selected.filter((id)=>id!==s.id))}/></td><td>{s.email}</td><td><StatusBadge t={t} status={s.status || "宸叉敞鍐?} /></td><td><PlanTypeBadge value={s.plan_type} /></td><td title={s.access_token || ""}>{tokenTailPreview(s.access_token)}</td><td title={s.refresh_token || ""}>{tokenPreview(s.refresh_token)}</td><td><div className="flex flex-wrap justify-center gap-2"><button className="sr-link" onClick={()=>setEditing(s)}>{t.edit}</button><button className="sr-link" onClick={()=>exp([s.id],"all")}>{t.export}</button><ConfirmBubble message={t.confirmDeleteMailbox} detail={s.email} onConfirm={()=>del(s)}><button className="sr-link text-red-500">{t.delete}</button></ConfirmBubble></div></td></tr>) : <tr><td colSpan={7}><div className="sr-empty !min-h-[260px]"><div className="sr-empty-icon"><Inbox className="h-7 w-7"/></div><p className="mt-3 text-sm text-slate-400">{t.noData}</p></div></td></tr>}</tbody></table>
    <PaginationBar t={t} total={total} page={page} pageSize={pageSize} setPage={setPage} setPageSize={setPageSize} />
    {editing && <SessionEditModal t={t} item={editing} onClose={()=>setEditing(null)} onSaved={()=>{setEditing(null); notify("ok", t.done); void load();}} notify={notify}/>}
  </Card>;
}

function SessionEditModal({ t, item, onClose, onSaved, notify }: { t: typeof zh; item: AnyObj; onClose:()=>void; onSaved:()=>void; notify:(type:"ok"|"fail", text:string)=>void }) {
  const [form,setForm]=useState<AnyObj>({...item});
  async function save() {
    try {
      await apiFetch(`/sunny/sessions/${item.id}`, { method:"PUT", body:JSON.stringify({ status:form.status, access_token:form.access_token, refresh_token:form.refresh_token, session_json:form.session_json }) });
      onSaved();
    } catch(e:any) { notify("fail", e.message || String(e)); }
  }
  return createPortal(<div className="sr-modal-mask"><div className="sr-modal sr-mailbox-modal">
    <div className="sr-modal-head"><h3>{t.edit} Session</h3><button onClick={onClose}><X className="h-5 w-5"/></button></div>
    <div className="sr-modal-body space-y-4">
      <div className="grid gap-4 md:grid-cols-2">
        <div><Label>{t.email}</Label><Input value={form.email || ""} disabled /></div>
        <div><Label>{t.status}</Label><SelectBox value={form.status||"宸叉敞鍐?} onChange={(v)=>setForm({...form,status:String(v)})} options={SESSION_STATUS_OPTIONS.map((s)=>({value:s,label:t.statusLabels[s as keyof typeof t.statusLabels] || s}))} /></div>
      </div>
      <div><Label>{t.accessToken}</Label><Textarea className="min-h-24 rounded-[14px]" value={form.access_token||""} onChange={(e)=>setForm({...form,access_token:e.target.value})}/></div>
      <div><Label>{t.sessionRefreshToken}</Label><Textarea className="min-h-20 rounded-[14px]" value={form.refresh_token||""} onChange={(e)=>setForm({...form,refresh_token:e.target.value})}/></div>
    </div>
    <div className="sr-modal-foot"><button onClick={onClose}>{t.cancel}</button><Button className="ml-3 rounded-xl bg-emerald-600 px-6 !text-white hover:bg-emerald-700" onClick={save}><Save className="mr-2 h-4 w-4"/>{t.save}</Button></div>
  </div></div>, document.body);
}
function logModuleLabel(t: typeof zh, module: string) {
  const map: Record<string,string> = { "浠ｇ悊": t.logProxy, "Proxy": t.logProxy, "閭": t.logMailbox, "Mailbox": t.logMailbox, "鎵嬫満": t.logPhone, "Phone": t.logPhone, "Session": t.logSession, "璁よ瘉": t.logAuth, "Auth": t.logAuth, "绯荤粺": t.logSystem, "System": t.logSystem };
  return map[module] || module;
}
function logStageLabel(t: typeof zh, value: any) {
  const text = String(value || "");
  if (text === REGISTER_ONLY || text.includes("浠呮敞鍐?) || /register chatgpt only/i.test(text)) return t.registerOnly;
  if (text === CODEX_PHONE_BIND || text.includes("鎺ョ爜") || /phone binding/i.test(text)) return t.codexPhoneBind;
  if (text === IMPORT_REVERSE_PROXY || text.includes("鍙嶄唬") || /reverse proxy/i.test(text)) return t.importReverseProxy;
  return text || "-";
}
function localizedLogMessage(t: typeof zh, entry: LogEntry) {
  const enMode = t.workbench === en.workbench;
  const msg = String(entry.message || "");
  const raw = String(entry.rawMessage || msg);
  const detail = entry.detail || {};
  const stage = logStageLabel(t, detail.stage || "");
  const nums = {
    total: Number(detail.total ?? 0),
    success: Number(detail.success ?? 0),
    failed: Number(detail.failed ?? 0),
    registered: Number(detail.registered ?? 0),
    loggedIn: Number(detail.logged_in ?? 0),
    skippedPhone: Number(detail.skipped_phone ?? 0),
    imported: Number(detail.imported ?? 0),
  };
  const proxyStats = detail.proxy_stats || {};
  const proxyText = String(detail.proxy || "").trim();
  const emailMatch = raw.match(/^\[([^\]\s]+@[^\]\s]+)\]/);
  const email = String(detail.email || emailMatch?.[1] || "");
  const prefix = "";
  const pick = (zhText: string, enText: string) => enMode ? enText : zhText;
  const externalRaw = () => msg;

  if (/SunnyRegister Worker accepted register task/i.test(msg)) return pick("SunnyRegister Worker 宸叉帴鏀舵敞鍐屼换鍔?, "SunnyRegister Worker accepted the register task");
  if (/鏈浠诲姟闃舵/.test(msg) || /task stage/i.test(msg)) {
    return pick(`鏈浠诲姟闃舵锛?{stage}锛岃处鍙锋暟閲忥細${nums.total || detail.total || "-"}`, `Task stage: ${stage}; accounts: ${nums.total || detail.total || "-"}`);
  }
  if (/娉ㄥ唽浠诲姟骞跺彂鏁?.test(msg) || /register task concurrency/i.test(msg)) {
    return pick(
      `娉ㄥ唽浠诲姟骞跺彂鏁帮細${detail.concurrency || "-"}锛涙瘡涓偖绠变娇鐢ㄧ嫭绔?Worker銆佹祻瑙堝櫒涓婁笅鏂囧拰閭楠岃瘉鐮佽鍙栧櫒`,
      `Register task concurrency: ${detail.concurrency || "-"}; each mailbox uses an isolated worker, browser context and mailbox OTP reader`,
    );
  }
  if (/娉ㄥ唽浠诲姟鎬荤粨/.test(msg) || /task summary/i.test(msg)) {
    return pick(
      `娉ㄥ唽浠诲姟鎬荤粨锛氭垚鍔?${nums.success}锛屽け璐?${nums.failed}锛屾柊娉ㄥ唽 ${nums.registered}锛岀櫥褰曟洿鏂?${nums.loggedIn}锛岃烦杩囨帴鐮?${nums.skippedPhone}锛屽鍏ュ弽浠?${nums.imported}`,
      `Register task summary: success ${nums.success}, failed ${nums.failed}, newly registered ${nums.registered}, login refreshed ${nums.loggedIn}, phone skipped ${nums.skippedPhone}, reverse-proxy imported ${nums.imported}`,
    );
  }
  if (/浠ｇ悊(?:姹??寮€鍏?.test(msg) || /proxy switch/i.test(msg)) {
    const open = detail.proxy_enabled !== false && !/鍏抽棴|off/i.test(msg);
    return pick(
      `浠ｇ悊姹犲紑鍏筹細${open ? "寮€鍚? : "鍏抽棴"}锛涗唬鐞嗘睜鎬绘暟 ${proxyStats.total ?? 0}锛屽惎鐢?${proxyStats.enabled ?? 0}锛屽仠鐢?${proxyStats.disabled ?? 0}锛屽け鏁?${proxyStats.invalid ?? 0}${open ? "" : "锛涙敞鍐屾満灏嗕娇鐢ㄦ湇鍔″櫒绯荤粺鍑哄彛"}`,
      `Proxy switch: ${open ? "on" : "off"}; pool total ${proxyStats.total ?? 0}, enabled ${proxyStats.enabled ?? 0}, disabled ${proxyStats.disabled ?? 0}, invalid ${proxyStats.invalid ?? 0}${open ? "" : "; SunnyRegister will use the server/system network outlet"}`,
    );
  }
  if (/娉ㄥ唽\/鐧诲綍璇锋眰灏嗕娇鐢ㄤ唬鐞嗗嚭鍙?.test(msg) || /requests? .*proxy/i.test(msg)) return pick(`娉ㄥ唽/鐧诲綍璇锋眰灏嗕娇鐢ㄤ唬鐞嗗嚭鍙ｏ細${proxyText || msg.split("锛?).pop() || "-"}`, `Register/login requests will use proxy outlet: ${proxyText || msg.split(":").pop() || "-"}`);
  if (/鏈幏鍙栧埌鍙敤浠ｇ悊/.test(msg)) return pick("鏈幏鍙栧埌鍙敤浠ｇ悊锛屾敞鍐屼换鍔″皢鍋滄锛涜鍏堟柊澧炲苟鍚敤浠ｇ悊锛屾垨鍏抽棴浠ｇ悊寮€鍏炽€?, "No usable proxy was found. The register task will stop; add and enable a proxy first, or turn off the proxy switch.");
  if (/寮€濮嬫敞鍐孿/鐧诲綍/.test(msg)) {
    const m = msg.match(/(\d+)\/(\d+)/);
    return pick(`${prefix}寮€濮嬫敞鍐?鐧诲綍 ${m?.[1] || "-"} / ${m?.[2] || "-"}锛岄樁娈碉細${stage || logStageLabel(t, msg)}`, `${prefix}Start register/login ${m?.[1] || "-"} / ${m?.[2] || "-"}; stage: ${stage || logStageLabel(t, msg)}`);
  }
  if (/娉ㄥ唽\/鐧诲綍娴侀噺浣跨敤浠ｇ悊姹犱唬鐞唡娉ㄥ唽\/鐧诲綍娴侀噺浣跨敤浠ｇ悊/.test(msg)) return pick(`${prefix}娉ㄥ唽/鐧诲綍娴侀噺浣跨敤浠ｇ悊姹犱唬鐞嗭細${proxyText || msg.split(":").pop() || "-"}`, `${prefix}Register/login traffic uses proxy-pool proxy: ${proxyText || msg.split(":").pop() || "-"}`);
  if (/娉ㄥ唽\/鐧诲綍娴侀噺浣跨敤鏈嶅姟鍣ㄧ郴缁熷嚭鍙ｄ唬鐞?.test(msg)) return pick(`${prefix}娉ㄥ唽/鐧诲綍娴侀噺浣跨敤鏈嶅姟鍣ㄧ郴缁熷嚭鍙ｄ唬鐞嗭細${proxyText || msg.split(":").pop() || "-"}`, `${prefix}Register/login traffic uses server/system outlet proxy: ${proxyText || msg.split(":").pop() || "-"}`);
  if (/娉ㄥ唽\/鐧诲綍娴侀噺浣跨敤鏈嶅姟鍣ㄧ郴缁熺綉缁滅洿杩炲嚭鍙娉ㄥ唽\/鐧诲綍娴侀噺浣跨敤鏈嶅姟鍣ㄧ郴缁熺綉缁滃嚭鍙?.test(msg)) return pick(`${prefix}娉ㄥ唽/鐧诲綍娴侀噺浣跨敤鏈嶅姟鍣ㄧ郴缁熺綉缁滅洿杩炲嚭鍙, `${prefix}Register/login traffic uses the server/system direct outlet`);
  if (/宸蹭粠鎺ョ爜閰嶇疆鍒嗛厤鎵嬫満鍙?.test(msg)) return pick(`${prefix}宸蹭粠鎺ョ爜閰嶇疆鍒嗛厤鎵嬫満鍙?${msg.match(/\+?\d{6,}/)?.[0] || ""}`.trim(), `${prefix}Allocated a phone number from SMS settings ${msg.match(/\+?\d{6,}/)?.[0] || ""}`.trim());
  if (/閭璁板綍宸叉湁 OpenAI RT/.test(msg)) return pick(`${prefix}閭璁板綍宸叉湁 OpenAI RT锛屽皢鐩存帴鍒锋柊 Session`, `${prefix}Mailbox already has OpenAI RT; refreshing Session directly`);
  if (/鑱斿姩.*鎺ョ爜閰嶇疆|鑷缓鎵嬫満鍙锋睜/.test(msg)) return pick(`${prefix}灏嗚仈鍔ㄢ€滄帴鐮侀厤缃€濈殑鑷缓鎵嬫満鍙锋睜瀹屾垚鎵嬫満楠岃瘉`, `${prefix}Will use the self-managed phone pool in SMS settings for phone verification`);
  if (/鏃犲彲鐢ㄦ墜鏈哄彿/.test(msg)) return pick(`${prefix}鏃犲彲鐢ㄦ墜鏈哄彿锛氭祦绋嬩粎瀹屾垚 ChatGPT 娉ㄥ唽/鐧诲綍锛屼笉杩涜鎺ョ爜锛屼篃涓嶄細鑾峰彇 Refresh Token銆俙, `${prefix}No usable phone number: this flow only completes ChatGPT register/login, without phone binding or Refresh Token acquisition.`);
  if (/寮€濮嬫敞鍐屾垨鐧诲綍/.test(msg)) return pick(`${prefix}寮€濮嬫敞鍐屾垨鐧诲綍${email ? `锛?{email}` : ""}`, `${prefix}Start register or login${email ? `: ${email}` : ""}`);
  if (/鎵ц鏂瑰紡锛?.test(msg)) {
    const isBackground = /鍚庡彴|Headless/i.test(msg);
    return pick(`${prefix}鎵ц鏂瑰紡锛?{isBackground ? "鍚庡彴娴忚鍣ㄨ嚜鍔紙鏃犵獥鍙ｏ級" : "鍙娴忚鍣ㄨ嚜鍔紙鏈夌獥鍙ｏ級"}`, `${prefix}Execution mode: ${isBackground ? "Background browser (headless)" : "Visible browser (headed)"}`);
  }
  if (/鏃犵棔娴忚鍣ㄤ笂涓嬫枃|娴忚鍣ㄦ棤鐥曚笂涓嬫枃/.test(msg)) return enMode ? msg.replace("宸插惎鍔ㄩ殧绂绘棤鐥曟祻瑙堝櫒涓婁笅鏂?, "Isolated incognito browser context started").replace("璇█鐜", "locale") : msg;
  if (/娴忚鍣ㄦ寚绾?.test(msg)) return enMode ? msg.replace("娴忚鍣ㄦ寚绾?, "Browser fingerprint") : msg;
  if (/宸叉墦寮€ OpenAI 璁よ瘉椤?.test(msg)) return pick("宸叉墦寮€ OpenAI 璁よ瘉椤碉紱濡傚嚭鐜颁汉鏈洪獙璇侊紝璇峰湪娴忚鍣ㄤ腑鎵嬪姩瀹屾垚", "Opened the OpenAI authentication page; if a challenge appears, complete it manually in the browser");
  if (/鎻愬墠杩炴帴 Outlook IMAP/.test(msg)) return pick("鎻愬墠杩炴帴 Outlook IMAP锛屽噯澶囨帴鏀?OpenAI 楠岃瘉鐮?, "Connected to Outlook IMAP in advance and prepared to receive the OpenAI verification code");
  if (/濉啓閭骞剁户缁?.test(msg)) return pick("濉啓閭骞剁户缁?, "Filled the email and continued");
  if (/绛夊緟 OpenAI 閭楠岃瘉鐮?.test(msg)) return pick("绛夊緟 OpenAI 閭楠岃瘉鐮?, "Waiting for the OpenAI email verification code");
  if (/宸叉彁浜ら偖绠遍獙璇佺爜/.test(msg)) return pick("宸叉彁浜ら偖绠遍獙璇佺爜", "Submitted the email verification code");
  if (/Cloudflare challenge/.test(msg) && /瑙﹀彂|鎵撳紑楠岃瘉椤?.test(msg)) return pick("EmailOtpValidate 瑙﹀彂 Cloudflare challenge锛屽凡鎵撳紑楠岃瘉椤?, "EmailOtpValidate triggered a Cloudflare challenge; verification page opened");
  if (/Cloudflare challenge 宸查€氳繃/.test(msg)) return pick("Cloudflare challenge 宸查€氳繃锛岄噸璇曢偖绠遍獙璇佺爜鎻愪氦", "Cloudflare challenge passed; retrying email code submission");
  if (/绛夊緟 Cloudflare 閫氳繃/.test(msg)) return enMode ? msg.replace("绛夊緟 Cloudflare 閫氳繃锛屽墿浣欑害", "Waiting for Cloudflare to pass, about").replace("褰撳墠 URL", "current URL") : msg;
  if (/璐﹀彿闇€瑕佸瘑鐮佹楠?.test(msg)) return pick("璐﹀彿闇€瑕佸瘑鐮佹楠わ紝宸插～鍐欏瘑鐮?, "Password step required; password filled");
  if (/濉啓鍩虹璧勬枡/.test(msg)) return enMode ? msg.replace("濉啓鍩虹璧勬枡", "Filled profile details") : msg;
  if (/鏈嶅姟瑕佹眰鐢佃瘽楠岃瘉/.test(msg)) return enMode ? msg.replace("鏈嶅姟瑕佹眰鐢佃瘽楠岃瘉锛屽凡濉啓鎵嬫満鍙?, "Service requested phone verification; phone number filled") : msg;
  if (/浠呮敞鍐岄樁娈?.test(msg)) return pick("浠呮敞鍐岄樁娈碉細宸茶鍙?ChatGPT Session锛屼笉鎵ц Codex OAuth / 涓嶈幏鍙?Refresh Token", "Register-only stage: ChatGPT Session was read; Codex OAuth / Refresh Token acquisition was skipped");
  if (/宸茶幏鍙?Access Token 鍜?Refresh Token/.test(msg)) return pick("宸茶幏鍙?Access Token 鍜?Refresh Token", "Access Token and Refresh Token acquired");
  if (/鍦ㄥ綋鍓嶇櫥褰曟€佸彂璧?OAuth 鎺堟潈鑾峰彇 Refresh Token/.test(msg)) return pick("鍦ㄥ綋鍓嶇櫥褰曟€佸彂璧?OAuth 鎺堟潈鑾峰彇 Refresh Token", "Started OAuth authorization in the current login state to obtain Refresh Token");
  if (/宸茶幏鍙?OAuth 鎺堟潈 code/.test(msg)) return pick("宸茶幏鍙?OAuth 鎺堟潈 code锛屾鍦ㄤ氦鎹?Refresh Token", "OAuth authorization code acquired; exchanging for Refresh Token");
  if (/绛夊緟 OAuth callback/.test(msg)) return enMode ? msg.replace("绛夊緟 OAuth callback锛屽墿浣欑害", "Waiting for OAuth callback, about").replace("褰撳墠 URL", "current URL") : msg;
  if (/浣跨敤宸蹭繚瀛?OpenAI RT 鍒锋柊 Session/.test(msg)) return pick("浣跨敤宸蹭繚瀛?OpenAI RT 鍒锋柊 Session", "Refreshing Session with saved OpenAI RT");
  if (/娉ㄥ唽鎴栫櫥褰曞畬鎴?.test(msg)) return pick("娉ㄥ唽鎴栫櫥褰曞畬鎴愶紝宸茶鍙?Session 淇℃伅", "Register/login completed; Session information read");
  if (/璇嗗埆涓?*鎴愬姛/.test(msg)) {
    const isRegister = /娉ㄥ唽/.test(msg);
    const withRT = /Refresh Token/.test(msg);
    return pick(`${prefix}璇嗗埆涓?{isRegister ? "娉ㄥ唽" : "鐧诲綍"}鎴愬姛锛屽凡淇濆瓨 ChatGPT Session${withRT ? " 鍜?Refresh Token" : ""}`, `${prefix}Detected successful ${isRegister ? "registration" : "login"}; saved ChatGPT Session${withRT ? " and Refresh Token" : ""}`);
  }
  if (/娌℃湁 Refresh Token.*sub2api|娌℃湁 Refresh Token/.test(msg)) return pick(`${prefix}娌℃湁 Refresh Token锛屽凡鍋滄瀵煎叆 sub2api`, `${prefix}No Refresh Token; sub2api import stopped`);
  if (/瀵煎叆.*sub2api/.test(msg)) return pick(`${prefix}宸叉牴鎹弽浠ｉ厤缃鍏?sub2api`, `${prefix}Imported into sub2api according to reverse-proxy settings`);
  if (/鎵嬫満.*浜屾楠岃瘉|Phone verification required/i.test(msg)) return pick(`${prefix}璐﹀彿闇€瑕佹墜鏈哄彿浜屾楠岃瘉锛屼絾褰撳墠娌℃湁鍙敤鎺ョ爜閰嶇疆锛屾祦绋嬪凡鍋滄`, `${prefix}The account requires phone verification, but no usable SMS configuration is available; the flow stopped`);
  if (/Session\/RT .*鍒锋柊瀹屾垚|Session\/RT 鍒锋柊瀹屾垚/.test(msg)) return pick(`${prefix}Session/RT 鍒锋柊瀹屾垚`, `${prefix}Session/RT refresh completed`);
  if (/SunnyRegister Worker failed:/i.test(msg)) return pick(`SunnyRegister Worker 鎵ц澶辫触锛?{msg.replace(/SunnyRegister Worker failed:\s*/i, "")}`, `SunnyRegister Worker failed: ${msg.replace(/SunnyRegister Worker failed:\s*/i, "")}`);
  return externalRaw();
}
function LogCard({ t, title, logs, busy, onClear }: { t: typeof zh; title: string; logs: LogEntry[]; busy: boolean; onClear: () => void }) {
  return <Card className="sr-log-card rounded-[30px] p-5">
    <div className="mb-4 flex items-center justify-between">
      <h2 className="text-xl font-black text-[#063b36] dark:text-emerald-50">{title}</h2>
      <div className="flex items-center gap-2">
        <button type="button" className="sr-log-clear-btn" onClick={onClear} disabled={!logs.length}>{t.clearLogs}</button>
        {busy?<Loader2 className="h-5 w-5 animate-spin text-[var(--accent)]"/>:<Settings2 className="h-5 w-5 text-[var(--accent)]"/>}
      </div>
    </div>
    <div className="log-box sr-paylink-log rounded-[24px] p-4">
      {logs.length ? logs.map((x)=><div key={x.id} className={cn("sr-log-line", `level-${x.level}`)}>
        <div className="sr-log-meta">
          <span className="sr-log-time">[{x.time}]</span>
          {x.email ? <span className="sr-log-email">[{x.email}]</span> : null}
          <span className="sr-log-module">[{logModuleLabel(t, x.module)}]</span>
        </div>
        <div className="sr-log-message">{localizedLogMessage(t, x)}</div>
      </div>) : <div className="sr-log-empty">{t.noLogs}</div>}
    </div>
  </Card>;
}


























