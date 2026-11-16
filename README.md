# Universal Video Hub · 全網影音下載器

整合 **PyQt6 桌面版**、**FastAPI + WebSocket 行動網頁版** 與 **GitHub Pages 手機前端** 的多平台影音下載器專案，底層採用 `yt-dlp` 與 `FFmpeg`。

## 主要功能

- **多平台支援**：YouTube / Shorts、Bilibili（自動影音合併與 HTTP 412 防護標頭）、Instagram、Threads（支援 `/share/` 短連結重導向與 `injected_media_ids` 反推 Shortcode + CDN Progressive MP4 嗅探）、X / Twitter（自動轉換 `vxtwitter`/`fxtwitter`/`fixupx` 代理網域）、TikTok 等。
- **高畫質影音合併**：優先選擇 `MP4 + M4A (AAC)` 串流並透過 `FFmpeg` 自動合併，解決 YouTube Shorts / Bilibili 影音分離或播放器無聲問題。
- **彈性 Cookie 認證**：
  - 桌面版保留「一鍵抓取瀏覽器 Cookie」（含 Chrome/Edge App-Bound 加密提示與 Firefox 支援）。
  - 支援手動匯入 Netscape `cookies.txt`，遇到 Meta（IG/Threads）網站會自動純化 Session Cookie，減少無關網域衝突。
  - 未設定 Cookie 時自動安全降級為訪客模式（不會因缺少 Firefox 設定檔而直接中斷）。
- **播放清單支援**：可選啟用播放清單與範圍指定（如 `1-5`）。
- **行動裝置 / GitHub Pages 支援**：
  - GitHub Pages 靜態入口（`docs/index.html`）支援「免伺服器直連通道」以及「連線遠端 FastAPI 雲端後端」。
  - 內建 Docker / Render 部署配置（`deploy/`），支援透過環境變數 `COOKIES_DATA` 注入雲端憑證並於下載後自動清理暫存檔。

---

## 專案目錄結構

```text
video_downloader/
├── main.py                      # PyQt6 桌面版主程式入口
├── web_app.py                   # FastAPI 手機 / 網頁版啟動入口
├── src/
│   ├── core/                    # 桌面與網頁共用核心引擎
│   │   ├── downloader.py        # UniversalDownloaderEngine、URL 正規化、Meta 救援
│   │   ├── cookies.py           # 瀏覽器 Cookie 提取與 Meta Cookie 純化
│   │   └── ffmpeg_helper.py     # FFmpeg / FFprobe 智慧路徑偵測
│   ├── desktop/                 # PyQt6 GUI、樣式與非同步工作執行緒
│   └── web/                     # FastAPI + WebSocket 後端服務
├── docs/                        # GitHub Pages 靜態手機前端 (index.html)
├── deploy/                      # Dockerfile、render.yaml、精簡版 requirements.txt
├── packaging/                   # PyInstaller 封裝設定檔 (UniversalVideoHub.spec)
├── tests/                       # 單元測試與 Web API 測試
├── archive/                     # 歷史開發版本 (v1 ~ v24)
└── 製作影片下載器程式指南/       # 原始設計紀錄 (chat.md)
```

---

## 快速開始

### 1. 安裝相依套件

```powershell
pip install -r requirements.txt
```

> 若要合併 1080p 以上高畫質串流或轉檔 MP3，建議安裝系統 `ffmpeg`（例如 Windows 放於 `C:\ffmpeg\bin\ffmpeg.exe`），程式亦會自動偵測 `imageio-ffmpeg` 作為備援。

### 2. 啟動桌面版（PyQt6 GUI）

```powershell
python main.py
```

### 3. 啟動本機手機 / 網頁版（區網 Wi-Fi 共用）

```powershell
python web_app.py
```

啟動後終端機會顯示本機與區域網路網址（例如 `http://192.168.1.x:8000`），手機連上同一 Wi-Fi 後直接開啟該網址即可使用。

---

## GitHub Pages 手機版與雲端部署

### 啟用 GitHub Pages

1. 前往 GitHub 儲存庫 **Settings → Pages**。
2. **Source** 選擇 **GitHub Actions**（本專案已附 `.github/workflows/pages.yml`）或選擇 **Deploy from a branch**（`main` 分支 `/docs` 資料夾）。
3. 啟用後可透過以下網址於手機開啟：
   `https://johntsai0320.github.io/video_downloader/`

> **說明**：GitHub Pages 僅託管靜態 HTML/JS，無法直接在 GitHub 伺服器執行 `yt-dlp` 與 `FFmpeg`。在手機上開啟 GitHub Pages 後，可直接使用「免伺服器直連通道」，或在「遠端 FastAPI 後端伺服器設定」填入部署好的 HTTPS 後端網址（如 Render）。

### 部署後端至 Render（免費 Docker 容器）

1. 在 Render 建立 **New Web Service** 並連結此 GitHub 儲存庫。
2. 指向 `deploy/render.yaml`（或手動指定 Dockerfile 路徑 `deploy/Dockerfile`、Docker Context `.`）。
3. 如需下載登入限定內容，可在環境變數新增 `COOKIES_DATA`（貼入 `cookies.txt` 內容）。
4. 部署完成後取得 `https://<your-service>.onrender.com`，填入 GitHub Pages 的遠端後端設定即可連線使用。

---

## 封裝 Windows 執行檔 (.exe)

```powershell
pyinstaller --noconfirm packaging/UniversalVideoHub.spec
```

打包產物位於 `dist/UniversalVideoHub/UniversalVideoHub.exe`（已內含 `yt-dlp` 全部提取器、`ffmpeg.exe` 與 `ffprobe.exe`）。

---

## 執行測試

```powershell
python -m unittest discover -s tests -v
```
