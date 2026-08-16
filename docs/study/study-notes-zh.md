# 研究執行筆記（中文）

給主持人（facilitator）看的。從零開始到跑完一位受試者，需要碰到的東西全在這裡。
英文版在 `running-the-study.md`，內容一樣，這份比較短。

**兩個網址，記這兩個就好：**

| 誰 | 網址 |
|---|---|
| 主持人（你） | `https://sem-git.firebaseapp.com/admin` |
| 受試者 | `https://sem-git.firebaseapp.com/p/<他的 code>` |

---

## 這個研究在比什麼

同一個人做兩輪，每輪 45 分鐘，中間換專案也換工具：

- 一輪用 **plain git**（baseline）
- 一輪用 **sgt**（我們的工具）

兩輪都可以用 Claude Code。受試者從頭到尾**不知道**哪個是我們的工具 —— 畫面上只叫
**Setup A** 和 **Setup B**。

12 位受試者分成 4 個 counterbalancing group（工具順序 × 專案順序），
所以就算只做到 8 位，每組還是 2 位，資料依然平衡。

---

## 第一部分：整個研究只做一次

全部在 `/admin` 的 **Setup** 分頁。

### 1. 登入

用 Google 登入。`ryanyen2@mit.edu` 已經寫死在權限檔（`firestore.rules`）裡，不用設定。
要加其他人 → **Setup → Who else can see this console**。
⚠️ 加進去的人看得到全部受試者資料 **和答案卷**。

### 2. 上傳答案卷（answer key）

**Setup → Answer key → Load answer key JSON**，選 `docs/study/answer-key.json`。

裡面是 22 個 episode、quiz 答案、評分 rubric。
**故意不編進網站程式碼裡** —— 不然受試者打開 devtools 就看得到答案。

### 3. 設定 API keys

**Setup → Session keys**，三格：

| 欄位 | 用途 |
|---|---|
| Anthropic API key | 給 Claude Code 用 |
| OpenAI API key | 給 sgt 的自然語言選取、feature 命名用 |
| Model id | 整個研究固定同一個 |

- **一定要開專用 key + 設 spend cap。絕對不要用你自己的 key。**
- 受試者的電腦會自動抓這兩把 key，所以他們**不用自己貼 key**，也**不會用到自己的帳號額度**。
- 每位做完 → 在 Participants 分頁按 **Revoke**，然後**去 OpenAI / Anthropic 後台也真的撤銷**
  （網站上的按鈕只是標記狀態）。

### 4. 填受試者看得到的文字

**Setup → Participant-facing settings**：聯絡 email、報酬說明、IRB protocol 編號、
consent 說明書、四個 bundle 下載連結。

⚠️ **bundle 檔名不能出現 `sgt`**。受試者看得到網址，看到就破功了。
（現在的檔名是 `study-coursecraft-a.tgz` / `-b.tgz`，a/b 不透露哪個是哪個。）

### 5. 建 bundle + 發佈（一個指令）

```bash
scripts/publish-study.sh
```

這一個指令會：建四個 bundle → 建網站 → 上線 → **回頭抓線上的檔案比對大小**，
確認participants 現在下載到的就是剛剛建的東西。

> ⚠️ **常見誤解，很重要：**
> `npm run build && firebase deploy` **不會**重建 bundle。
> `npm run build` 建的是**網站**；bundle 是四個獨立的 `.tgz`，
> deploy 只是順手把 `web/public/bundles/` 裡「已經存在」的檔案一起送上去。
>
> 所以如果你改了 sgt 然後只跑 deploy → **網站是新的，受試者下載到的工具是舊的**，
> 而且畫面上完全看不出來。`publish-study.sh` 就是為了讓這件事不可能發生。

只有四個 bundle，不是每人一個。「這個人是哪一輪、他的 key 是什麼」是安裝腳本用他的
code 去線上抓的，所以 bundle 可以重複用。

建的時候會自動：跑測試（沒過就拒絕出貨）、預熱 sgt 的歷史檢視（受試者第一個指令才不會卡）、
附一個**跟兩個正式專案都不一樣**的練習用 repo。

⚠️ **有未 commit 的改動時，這個指令會直接拒絕執行** ——
否則 bundle 裡的版本會標成 `-dirty`，之後就無法回答「P07 跑的是哪一版」。

只改了網站文字、不想重建 bundle → `scripts/publish-study.sh --site`。

### 6. 建 12 位受試者

**Participants → Create 12。**

一次生成 12 筆，自動輪流分派到 4 組。每人一組 24 字元的 code，
**連結本身就是他的密碼**，當密碼看待。

招募到人之後，把 email 打進該列（每格現在會顯示是誰的，例如 `P03 email`），再複製連結給他。

> 📌 **系統不會自動寄信。** email 欄位只是紀錄用，信要你自己寄。
> 但還是要填對 —— 之後 consent、報酬、資料都是靠這一列對人的。

---

## 想先試跑？用 pilot，不要用真的受試者

**Participants → Add pilot** → 產生 `X01`（不是 `P01`）。

跟真的受試者**完全一樣**：真連結、真 key、真 bundle、真 telemetry。差別只有：

- ✅ 不會進 Results（除非你自己勾 *Include the pilot records*，勾了整頁會出現警告）
- ✅ 不會算進 12 人的人數和組別平衡
- ✅ **不管跑過幾個 pilot，`Create 12` 出來的永遠是 P01–P12**
- ✅ 受試者自己的頁面上每一步都有 **rehearsal** 標籤（發錯連結他自己看得到）
- ✅ 開過之後還可以刪掉（真的受試者一旦開過就不給刪）

用來測那些「只有在正式站上才會出事」的東西：Google 登入、bundle 下載、key 有沒有真的傳到電腦上。

如果連資料庫都不想碰 → 用 emulator（見英文版 §6），會有橘色 "Rehearsal mode" 橫幅。

---

## 第二部分：每位受試者

### 前一天：把連結寄給他

自己寄信，內容大概是：

> 我們約的時間之前，麻煩先開這個連結，把前面幾頁做完：一份 consent、幾題背景問卷、
> 還有一個安裝步驟。安裝大概幾分鐘，它會自己下載需要的 Python，**不會動到你電腦上其他東西**。
> 做到「練習」那一頁就停下來。
> 如果有任何一項變紅色，**直接跟我說，不要自己修** —— 我們在正式開始前處理掉會比較好。

**要給他的東西只有兩樣：**
1. 他的專屬連結 `https://sem-git.web.app/p/<code>`
2. 上面那段話

其他（bundle、key、指令）他都會從網頁上拿到。

安裝步驟最後有一個自動打勾的清單：Python、專案測試、歷史工具、
Claude Code profile、API key、**以及一次真的呼叫 AI**。
最後那項是重點 —— 它會抓到「key 有填但是是錯的」這種問題。

### 當天：把 Live 分頁開著

**Live** 會顯示每個人：現在在哪一步、目前這題倒數多久、瀏覽器有沒有連線、
**電腦有沒有在回報**，以及最近 24 筆動作。

| 狀況 | 怎麼處理 |
|---|---|
| 「their machine」變紅 | 請他確認 session shell 還開著，然後跑 `study-sync`。資料不會掉，只是你暫時看不到。 |
| 中途被打斷 / 出事 | 請他按 **Pause the clock** 並選原因。分析用的是實際作業時間。 |
| 連結打不開了 | 清了快取 / 換瀏覽器 / 無痕視窗都會被鎖（防止一個連結兩個人用）。開他的紀錄 → **Release link**。 |
| 有指令失敗 | Live 卡片上會直接顯示紅色的失敗指令（這是 pilot 03 之後才加的） |
| 要讓某人**重來** | 名冊每一列都有 **Reset**：清掉他做過的所有東西，但**連結和條件順序不變**（這是維持平衡的關鍵），回到第一步。 |
| 建錯了要**刪掉** | 同一列的 **Delete**：連人帶資料整個移除，連結失效。 |

> Reset / Delete 兩個都會先**數出將要刪掉什麼**（例如「1 responses, 5 events, 1 scoring」）再要你確認。
> 兩個在**任何狀態**下都能用 —— 舊版只有 `created` 狀態能刪，所以一個已經 `consented` 的
> 測試帳號會完全刪不掉。
>
> 要一次清空（正式開始前清測試資料）→ 展開 **Danger zone**，
> 有「只刪 pilot」「只刪正式受試者」「全刪」三個選項。

### 資料會不會掉？

不會。問卷答案、任務答案、訪談筆記都是**每打一個字就存到瀏覽器**，
再用 debounce 寫進資料庫 —— 關分頁、重整、當機、斷網，最多掉最後一個字。

唯一「不會自動寫進資料庫」的是**評分**：評分是判斷，半套的 rubric 不應該進資料。
它會存在你這台瀏覽器裡，下次打開那一題時問你要不要還原。

主持人這邊沒變的事：**過一半**和**剩兩分鐘**要報時；常講「我們在測這兩套工具，不是在測你」；
讓他持續講出他在想什麼（think aloud）。

### 做完之後

1. 按 **Revoke** 收回他的 key，**再去 provider 後台真的撤銷**
2. 請他跑 `study-sync --final`，再跑 `study-cleanup`
3. 進 **Requests & scoring** 評分

---

## 第三部分：評分

開某位受試者 → **Requests & scoring**。每一題會顯示他做了什麼、花多久、
有沒有超時、他自己的答案，旁邊就是標準答案。

第 2、3、4 題要跑腳本，輸出**原封不動貼回去**（那是分數的證據）：

```bash
python3 scripts/score_study_repo.py ~/study/p07/work \
    --baseline ~/repos/sgt-study/coursecraft \
    --expect-removed waitlist,promotion,notify \
    --expect-gone waitlist,notices
```

**Quiz & summary** 會自動對答案，並且產出三個數字：
講到幾個 episode、因果關係講對幾個、**講得很篤定但講錯幾個**。
只看第一個會變成獎勵「列清單」；三個一起看才分得出「背下來」和「真的懂」。

**Interview** 是訪談題目 + 逐字筆記。

> ⚠️ 第四題「你希望能問這段歷史什麼問題？」**一定要在他比較兩套工具之前問**。
> 兩次 pilot 都在這題講出很接近 sgt 在做的事，其中一位還是在 git 那一輪講的。問順序錯了就浪費掉。

---

## 第四部分：出圖

**Results → Compute from data**。

所有數字都是從原始事件流即時算出來的，沒有預先存好的統計值 ——
所以之後想改某個指標的定義，是改程式碼再重算，而不是「當初沒記到」。

三張圖，都可以匯出成 SVG（字是真的字，可以直接放論文）：

1. **兩套工具用起來感覺如何** — 10 題感受量表，diverging stacked bar + 配對平均差 + 95% CI
2. **他們實際做出了什麼** — 四個評分結果的 paired estimation plot，每位受試者一條線
3. **他們是怎麼做的** — 時間花在哪、以及最能區分兩組的操作 bigram（weighted log-odds）。
   這張圖回答「他們是不是只是比較會用 AI 而已」

下面有三個 CSV：每人每條件一列（跑 mixed model 用）、每題一列、編碼過的操作序列（質性分析用）。

**Show example data** 會用 12 人的假資料把圖填滿 ——
**在第一位受試者之前**就先用它檢查圖和匯出對不對，不要事後才發現。

---

## 還沒解決、你要決定的事

### 🔴 `Sgt-Op` commit trailer（會影響論文能宣稱什麼）

sgt 那一輪的專案裡，每個 commit 訊息底下都掛著一大串操作編號 —— 實測某個 commit
真正的訊息只有一行，底下卻有 **174 行** `Sgt-Op:`。git 那一輪的專案我當初清乾淨了。

結果是：**在 sgt 那一輪，plain git 被弄得比較難用**，
而這個偏差的方向剛好對 sgt 有利。三個選項：

1. 把編號搬到 git notes，commit 訊息就乾淨了（最正確，但測試專案要重建）
2. 在 sgt 那輪加一個 git 設定讓歷史好讀（快，但也算改動了實驗條件，一樣要揭露）
3. 不改，在論文 limitations 老實寫，附上 174 這個數字

**不管選哪個，第 3 項都一定要做，而且要在第一位受試者之前決定。**

### 🟡 其他

- 建好 12 人**之後**再改 API key，改的不會傳到已建立的人身上，要一個一個按 **Re-issue**。
  而且 Revoke → Re-issue 這條補救路徑**從來沒有人真的測過** —— 先用 pilot 測一次。
- 兩個專案是同構的（同一套 episode 劇本換名詞）。pilot 受試者說第二輪「明顯在套第一輪的答案」。
  設計上已經用 counterbalancing 處理，但要記得在分析裡把 **order 當作固定效果**，
  而且 12 人樣本下這個 carry-over 可能比條件本身的效果還大 —— 這要寫進分析計畫，不要事後才講。

---

## 詞彙對照

| 英文 | 這裡的意思 |
|---|---|
| condition | 兩種工具之一（git / sgt） |
| block / half | 一輪 45 分鐘 |
| counterbalancing group | 決定工具順序和專案順序的分組（1–4） |
| bundle | 發給受試者的壓縮檔，解開就能跑 |
| request | 一題任務（R1–R6） |
| episode | 測試專案歷史裡的一次「改動事件」，共 22 個 |
| pilot / rehearsal | 試跑用的假受試者（X01…），不算進結果 |
| telemetry | 受試者電腦自動回報的操作紀錄 |
| answer key | 答案卷 + 評分標準 |
