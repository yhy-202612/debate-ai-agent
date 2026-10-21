/*
 * 人机辩论赛 · 抢话按钮（ESP32 · WiFi 版，备用方案）
 * ============================================================
 * 与 USB 版（esp32-button.ino）功能相同，但**不接电脑**：
 *   按下按钮 → HTTP POST /debate/api/buzzer → 服务器 → 大屏 / 智能体
 *
 * 什么时候用它：
 *   · 按钮离电脑太远，拉不了 USB 线
 *   · 想完全免线（用充电宝供电）
 *   ⚠ 它依赖现场 WiFi。赛场网络一旦抖动，抢话就没了 —— 所以只作「备用」，
 *     正式比赛仍建议 USB 版（零网络依赖、实测 ~0.1 秒）。
 *
 * 一台板子可以接 4 个按钮（BOARD '4'），也可以两块板各接 2 个（'A' / 'B'）。
 *
 * ★ 本文件已预置为「正方 侧」那块板（BOARD 'A'），按钮对应：正方一辩（GPIO 4）、正方三辩（GPIO 5）
 *   烧录前只需改顶部三处：WIFI_SSID / WIFI_PASS / SERVER_HOST，其余不用动。
 *
 * 接线（默认，可改）：
 *   按钮1  —— GPIO 4   —— 另一端接 GND
 *   按钮2  —— GPIO 5   —— 另一端接 GND
 *   按钮3  —— GPIO 6   —— 另一端接 GND   （仅 BOARD '4' 用）
 *   按钮4  —— GPIO 7   —— 另一端接 GND   （仅 BOARD '4' 用）
 *   蜂鸣器 —— GPIO 10  —— 有源蜂鸣器正极接 10，负极接 GND
 *   ★ ESP32-C3：GPIO18/19 是 USB 差分线，绝不能接外设（会把 USB 掐断）
 *   （INPUT_PULLUP，按下读到 LOW；用宠物语音按钮拆出来的轻触开关时，
 *     取其**对角两脚**，同侧两脚是常通的）
 *
 * 编译：Arduino IDE 选 ESP32 Dev Module；库只用自带的 WiFi / HTTPClient。
 */

/* ==================== 先改这里 ==================== */
#define WIFI_SSID     "你的WiFi名"
#define WIFI_PASS     "你的WiFi密码"

#define SERVER_HOST   "120.48.43.211"   // 辩论服务地址（不带 http://）
#define SERVER_PORT   80
#define SERVER_PATH   "/debate/api/buzzer"

// 'A' = 正方侧那块（2 个按钮）  'B' = 反方侧那块（2 个按钮）  '4' = 一块板接 4 个按钮
#define BOARD         'B'

#define HAS_BUZZER   0        // 1=接了有源蜂鸣器；0=没有（按下/提示改用板载 LED）
#define HAS_LED      1        // 1=用开发板自带的 LED 做提示（多数 ESP32 开发板是 GPIO2）
#define ENABLE_DOWNLINK 1     // 1=接收服务器指令（AI 说完提示一下）；0=只上传，不占连接
/* ================================================== */

#include <WiFi.h>
#include <HTTPClient.h>

/* ★ ESP32-C3 引脚禁区：GPIO18/19 = USB D-/D+（占用即断 USB）、GPIO20/21 = UART0、
   GPIO8/9 = strapping/BOOT、GPIO11~17 = 片上 Flash。以下已全部避开。 */
const int BUZZER = 10;        // 原为 18 —— C3 的 18 是 USB D-，会掐断 USB
const int LED    = 2;         // 开发板自带 LED（C3 各板不同，若判断不确定可把 HAS_LED 设为 0）
const int PIN_BTN[4] = { 4, 5, 6, 7 };   // 原为 {4,5,19,21} —— 19 是 USB D+、21 是 UART TX
const unsigned long DEBOUNCE_MS = 15;

struct Btn {
  int pin;
  const char* side;   // "pro" / "con"
  int seat;           // 1 / 3
  bool lastRead;
  bool state;
  unsigned long tEdge;
};

Btn btns[4];
int btnCount = 0;

String boardTag;      // "A" / "B" / "4"
long pollSeq = 0;     // 下行长轮询：已收到的最新事件序号

/* ---------------- 按钮表 ---------------- */
void buildButtons() {
  if (BOARD == 'A') {            // 正方侧：一辩 / 三辩
    boardTag = "A";
    btns[0] = { PIN_BTN[0], "pro", 1, HIGH, false, 0 };
    btns[1] = { PIN_BTN[1], "pro", 3, HIGH, false, 0 };
    btnCount = 2;
  } else if (BOARD == 'B') {     // 反方侧：一辩 / 三辩
    boardTag = "B";
    btns[0] = { PIN_BTN[0], "con", 1, HIGH, false, 0 };
    btns[1] = { PIN_BTN[1], "con", 3, HIGH, false, 0 };
    btnCount = 2;
  } else {                       // 单板四键：正一 / 正三 / 反一 / 反三
    boardTag = "4";
    btns[0] = { PIN_BTN[0], "pro", 1, HIGH, false, 0 };
    btns[1] = { PIN_BTN[1], "pro", 3, HIGH, false, 0 };
    btns[2] = { PIN_BTN[2], "con", 1, HIGH, false, 0 };
    btns[3] = { PIN_BTN[3], "con", 3, HIGH, false, 0 };
    btnCount = 4;
  }
}

/* ---------------- 提示（蜂鸣器 / 板载 LED，二选一或都关） ---------------- */
void setLed(bool on) {
#if HAS_LED
  digitalWrite(LED, on ? LOW : HIGH);      // 多数开发板 LED 低电平点亮
#endif
}
void buzz(unsigned int ms) {
#if HAS_BUZZER
  digitalWrite(BUZZER, HIGH); delay(ms); digitalWrite(BUZZER, LOW);
#endif
#if HAS_LED
  setLed(true); delay(ms); setLed(false);
#endif
  if (!HAS_BUZZER && !HAS_LED) delay(ms);  // 都没接也不阻塞调用方语义
}
/* AI 说完 / 服务器提示：明显一点的两次快闪 */
void hint() {
#if HAS_BUZZER
  buzz(150);
#endif
#if HAS_LED
  for (int i = 0; i < 3; i++) { setLed(true); delay(90); setLed(false); delay(90); }
#endif
}

/* ---------------- WiFi ---------------- */
void ensureWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;
  Serial.print("[wifi] connecting to "); Serial.println(WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);                 // 关省电，抢话要快
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  unsigned long t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 15000) delay(200);
  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("[wifi] ok, ip="); Serial.println(WiFi.localIP());
  } else {
    Serial.println("[wifi] 连接失败，稍后重试");
  }
}

/* ---------------- 上传一次按键 ---------------- */
bool postDown(const Btn &b) {
  if (WiFi.status() != WL_CONNECTED) { ensureWiFi(); if (WiFi.status() != WL_CONNECTED) return false; }
  HTTPClient http;
  String url = String("http://") + SERVER_HOST + ":" + SERVER_PORT + SERVER_PATH;
  if (!http.begin(url)) return false;
  http.setConnectTimeout(1500);
  http.setTimeout(2000);
  http.addHeader("Content-Type", "application/json");
  String body = String("{\"side\":\"") + b.side + "\",\"seat\":" + b.seat +
                ",\"action\":\"down\",\"source\":\"button\",\"board\":\"" + boardTag + "\"}";
  int code = http.POST(body);
  http.end();
  Serial.printf("[post] %s code=%d\n", body.c_str(), code);
  return code > 0 && code < 400;
}

/* ---------------- 下行长轮询（独立任务，避免阻塞按钮） ---------------- */
void downlinkTask(void *param) {
  for (;;) {
    if (!ENABLE_DOWNLINK || WiFi.status() != WL_CONNECTED) { vTaskDelay(1000 / portTICK_PERIOD_MS); continue; }
    HTTPClient http;
    String url = String("http://") + SERVER_HOST + ":" + SERVER_PORT +
                 "/debate/api/buzzer/poll?after=" + pollSeq + "&board=" + boardTag + "&timeout=20";
    if (!http.begin(url)) { vTaskDelay(1500 / portTICK_PERIOD_MS); continue; }
    http.setConnectTimeout(3000);
    http.setTimeout(26000);                     // 长轮询，略大于服务端 20s
    int code = http.GET();
    if (code == 200) {
      String body = http.getString();
      // 更新序号
      int si = body.indexOf("\"seq\":");
      if (si >= 0) {
        long v = body.substring(si + 6).toInt();
        if (v > pollSeq) pollSeq = v;
      }
      // AI 说完 / 服务器要求提示 → 响一声（真人听到就知道该按了）
      bool needBuzz = body.indexOf("ai_done") >= 0 || body.indexOf("\"source\":\"ai\"") >= 0
                      || body.indexOf("\"buzz\"") >= 0;
      if (needBuzz) hint();
    }
    http.end();
    vTaskDelay(30 / portTICK_PERIOD_MS);
  }
}

/* ---------------- setup / loop ---------------- */
void setup() {
  Serial.begin(115200);
  delay(200);
  buildButtons();
#if HAS_BUZZER
  pinMode(BUZZER, OUTPUT);
  digitalWrite(BUZZER, LOW);
#endif
#if HAS_LED
  pinMode(LED, OUTPUT); setLed(false);
#endif
  for (int i = 0; i < btnCount; i++) pinMode(btns[i].pin, INPUT_PULLUP);

  ensureWiFi();
  buzz(60);
  hint();
  Serial.print("[ready] board="); Serial.print(boardTag);
  Serial.print(" btns="); Serial.println(btnCount);

  if (ENABLE_DOWNLINK) {
    // 下行长轮询放独立任务：它每次会挂起 ~20 秒，绝不能放在 loop 里阻塞按钮
    xTaskCreatePinnedToCore(downlinkTask, "downlink", 6144, NULL, 1, NULL, 1);
  }
}

void loop() {
  // 断网自动重连（每 5 秒试一次，不阻塞按钮扫描）
  static unsigned long lastTry = 0;
  if (WiFi.status() != WL_CONNECTED && millis() - lastTry > 5000) {
    lastTry = millis();
    WiFi.reconnect();
  }

  for (int i = 0; i < btnCount; i++) {
    bool raw = digitalRead(btns[i].pin);
    if (raw != btns[i].lastRead) {
      btns[i].lastRead = raw;
      btns[i].tEdge = millis();
    } else if (millis() - btns[i].tEdge >= DEBOUNCE_MS) {
      bool pressed = (raw == LOW);
      if (pressed != btns[i].state) {
        btns[i].state = pressed;
        if (pressed) {
          bool ok = postDown(btns[i]);
          if (!ok) { delay(120); postDown(btns[i]); }   // 失败重试一次
          buzz(40);                                     // 本地确认音
        }
      }
    }
  }
  delay(2);
}
