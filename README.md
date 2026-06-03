# 🎭 Virtual Streamer — AI 虛擬主播直播系統

> 兌心科技｜AI 驅動的虛擬主播自動化直播解決方案

以 Claude AI 為腳本大腦、ElevenLabs 為語音引擎、LiveAvatar 渲染虛擬人形象，實現全自動、可擴展的 AI 直播。

---

## 系統架構

```
┌─────────────────────────────────────────────────────────┐
│                    內容層（腳本管理）                    │
│         虛擬角色人格  ·  腳本庫  ·  直播規劃            │
└─────────────────────────┬───────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────┐
│                    執行層（自動化引擎）                  │
│                                                         │
│   ┌─────────────┐   ┌──────────────┐   ┌────────────┐  │
│   │ Claude API  │──▶│ ElevenLabs   │──▶│ LiveAvatar │  │
│   │（腳本大腦） │   │（語音合成）  │   │（虛擬人）  │  │
│   └─────────────┘   └──────────────┘   └─────┬──────┘  │
│                                               │         │
│   ┌───────────────────────────────────────────▼──────┐  │
│   │          LiveKit（即時音視訊傳輸）               │  │
│   └───────────────────────────────────────────────────┘  │
└─────────────────────────┬───────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────┐
│               串流輸出（OBS → 直播平台）                │
│         TikTok Live  ·  YouTube  ·  自定義平台          │
└─────────────────────────────────────────────────────────┘
```

**技術堆疊**

| 組件 | 技術 | 說明 |
|------|------|------|
| 腳本引擎 | Claude API (claude-opus-4-6) | 從腳本庫選取或即時生成台詞 |
| 語音合成 | ElevenLabs TTS | PCM 16kHz 高品質語音 |
| 虛擬人渲染 | LiveAvatar LITE Mode | 即時嘴型同步，1 credit/分鐘 |
| 音視訊傳輸 | LiveKit | WebRTC 低延遲串流 |
| 直播輸出 | OBS Studio | 推流至各大直播平台 |

---

## 三位虛擬主播

### 🎭 KIRA（凱拉）— 活潑親切型
> *「欸這個我要說！」「等等等等——」「這不是我說，是真的」*

- **定位**：Z 世代科技感，像閨蜜不像播音員
- **說話風格**：短句、跳躍、夾英文（literally / OMG / cute）
- **最強場景**：科技產品介紹 · 生活好物帶貨 · 互動遊戲

---

### 🎭 LEON（里昂）— 專業權威型
> *先說結論，再說原因。數據優先，不說廢話。*

- **定位**：虛擬分析師，冷靜有深度
- **說話風格**：邏輯清晰、精準用詞、偶爾一句讓人印象深刻的話
- **最強場景**：投資財經分析 · 科技深度解說 · B2B 產品展示

---

### 🎭 MOMO（桃桃）— 可愛療癒型
> *「嗯嗯」「好喜歡」「你們懂那種感覺嗎」*

- **定位**：虛擬生活風格創作者，溫柔陪伴感
- **說話風格**：輕柔、溫暖、會記住觀眾說的話
- **最強場景**：生活風格 · 美妝保養 · 情感陪伴

---

## 快速開始

### 1. 環境需求

- Python **3.10+**
- macOS（備用 TTS 使用系統 `say` 指令）或 Linux
- [LiveAvatar](https://www.liveavatar.com) 帳號（LITE Mode）
- [ElevenLabs](https://elevenlabs.io) API Key（付費方案支援 TTS）
- [Anthropic](https://console.anthropic.com) API Key（腳本即時生成）

### 2. 安裝

```bash
git clone https://github.com/pennyhuang-oss/virtual-streamer.git
cd virtual-streamer

pip install -r requirements.txt
```

### 3. 設定環境變數

```bash
cp .env.example .env
```

編輯 `.env`，填入各項 API Key（詳見下方說明）。

### 4. 啟動直播

```bash
# 基本用法
python main.py --avatar katya --theme AI產品介紹

# 其他範例
python main.py --avatar kira  --theme 生活好物分享
python main.py --avatar leon  --theme 2026年科技趨勢
python main.py --avatar momo  --theme 夏日美妝推薦
```

按 `Ctrl+C` 結束直播，系統自動執行結尾台詞並關閉 session。

---

## 環境變數說明

複製 `.env.example` 為 `.env` 後填入以下內容：

```bash
# ── LiveAvatar ─────────────────────────────────────────
# 取得方式：liveavatar.com → Settings → API Keys
LIVEAVATAR_API_KEY=your_liveavatar_api_key_here

# ── Anthropic Claude ───────────────────────────────────
# 取得方式：console.anthropic.com → API Keys
# 用途：腳本庫無匹配時即時生成台詞（選填）
ANTHROPIC_API_KEY=your_anthropic_api_key_here

# ── ElevenLabs ─────────────────────────────────────────
# 取得方式：elevenlabs.io → Profile → API Keys
# 注意：需付費方案才能使用 TTS；未設定則自動 fallback 至 macOS 內建語音
ELEVENLABS_API_KEY=your_elevenlabs_api_key_here

# ── Avatar IDs ─────────────────────────────────────────
# 取得方式：LiveAvatar Dashboard → Avatars → 點選 Avatar → 複製 ID
KATYA_AVATAR_ID=65cca4cf-b7c8-4619-871f-84e2cf8b21d4

# ── ElevenLabs Voice IDs ───────────────────────────────
# 取得方式：elevenlabs.io → Voices → 複製 Voice ID
# 留空則自動使用 macOS TTS 作為備用（僅供測試）
KATYA_VOICE_ID=
KIRA_VOICE_ID=
LEON_VOICE_ID=
MOMO_VOICE_ID=
```

---

## 專案結構

```
virtual-streamer/
│
├── main.py                   # 主程式入口
├── config.yaml               # 系統設定（timer、模型等）
├── requirements.txt          # Python 依賴
├── .env.example              # 環境變數範本
│
├── agents/
│   └── script_agent.py       # Claude 腳本引擎（選腳本 / 即時生成）
│
├── avatars/
│   ├── katya.yaml            # Katya 角色設定（Avatar ID、語音、人格）
│   ├── kira.yaml             # KIRA 角色設定（待建）
│   ├── leon.yaml             # LEON 角色設定（待建）
│   └── momo.yaml             # MOMO 角色設定（待建）
│
├── scripts/                  # 腳本庫
│   └── katya/
│       ├── 01_opening/       # 開場白
│       ├── 02_main/          # 主要內容
│       ├── 03_interaction/   # 觀眾互動
│       ├── 04_transition/    # 轉場
│       ├── 05_cta/           # 行動呼籲
│       └── 06_closing/       # 結尾
│
├── tts/
│   └── elevenlabs.py         # ElevenLabs TTS + macOS 備用
│
├── liveavatar/
│   └── session.py            # LiveAvatar LITE Mode session 管理
│
└── logs/
    └── sessions/             # 每場直播的腳本記錄（.jsonl）
```

---

## 腳本引擎邏輯

```
觸發事件（Timer / 新觀眾 / 禮物 / 問題）
           │
           ▼
  從對應資料夾隨機選腳本
           │
  找到？──YES──▶ 填入動態變數（主題、觀眾名）
     │
    NO
     │
     ▼
  呼叫 Claude API 即時生成台詞
           │
           ▼
  ElevenLabs TTS → PCM 音訊
           │
           ▼
  LiveKit 推送 → Avatar 嘴型同步
```

**觸發類型對應目錄：**

| 觸發 | 資料夾 | 間隔 |
|------|--------|------|
| `opening` | `01_opening/` | 直播開始時 |
| `main` | `02_main/` | 每 2 分鐘 |
| `new_viewer` | `03_interaction/greet/` | 偵測到新觀眾 |
| `cta` | `05_cta/` | 每 5 分鐘 |
| `closing` | `06_closing/` | Ctrl+C 時 |

---

## 費用試算（LITE Mode）

> LiveAvatar LITE Mode：**1 credit = 1 分鐘**直播時間

| 直播規格 | Credits 消耗 | Scale 方案（$475/月） | 備註 |
|----------|------------|----------------------|------|
| 1 小時/次 | 60 | 月額內 | |
| 每天 2 小時 × 30 天 | 3,600 | 月額內 | |
| 每天 4 小時 × 30 天 | 7,200 | 超額 2,200，+$220 | 總計約 $695/月 |
| 每天 8 小時 × 30 天 | 14,400 | 超額 9,400，+$940 | 建議洽談 Enterprise |

> ⚠️ Scale 方案單次 session 最長 **60 分鐘**。系統已內建自動每 **55 分鐘**重建 session。

---

## 查看直播畫面

1. 登入 [LiveAvatar Dashboard](https://www.liveavatar.com)
2. 進入 **Sessions** → 找到 Active session
3. 複製 LiveKit URL，貼入 OBS **Browser Source**
4. 或直接在 Dashboard 預覽視窗觀看

---

## 開發路線圖

- [x] MVP：Katya 單人直播（LiveAvatar + 腳本引擎 + 音訊推送）
- [x] 自動 session 重建（55 分鐘）
- [x] macOS TTS 備用（開發測試用）
- [ ] KIRA / LEON / MOMO 三角色完整建置
- [ ] ElevenLabs 語音克隆整合
- [ ] 觀眾留言即時偵測（TikTok / YouTube API）
- [ ] Flask Dashboard（Credits 監控 + 腳本記錄 UI）
- [ ] 多語言支援（英文、簡體中文）
- [ ] 多主播輪播模式

---

## License

MIT © 2026 兌心科技股份有限公司
