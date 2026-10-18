/*
 * 人机辩论赛 · 抢话按钮（ESP32 · USB 串口版）
 * ============================================================
 * 数据线连电脑 → 浏览器（大屏页面）通过 Web Serial 直接读取按钮事件
 *   ESP32 只做「中转」：按钮 → 串口 → 大屏网页 → 亮灯/计时/抢话仲裁 → （可选）转发给智能体
 * 零网络依赖，实测约 0.1 秒，正式比赛首选这个版本。
 *
 * ★ 本文件已预置为「反方 侧」那块板（BOARD 'B'），按钮对应：反方一辩（GPIO 4）、反方三辩（GPIO 5）
 *   烧录前不需要改任何东西；要改引脚就改 PIN_BTN。
 *
 * 接线（默认引脚）：
 *   按钮1  —— GPIO 4   —— 另一端接 GND
 *   按钮2  —— GPIO 5   —— 另一端接 GND
 *   （一板四键另加 GPIO 19 / 21，见 BOARD '4' 说明）
 *   蜂鸣器 —— GPIO 18  —— 可选；没有蜂鸣器就保持 HAS_BUZZER 0，改用板载 LED
 *   （INPUT_PULLUP，按下读到 LOW，不需要电阻）
 *
 * 通信协议（115200，每行一条，\n 结尾）：
 *   板上报： A1:D / A1:U      板号+按钮号 : D按下 / U松开
 *            READY A          上电握手（大屏可据此确认板子活着）
 *            A:PONG           回应电脑的 A:PING
 *   电脑下发：A:BUZZ            蜂鸣/LED 提示（大屏可对单块板定向响）
 *            A:PING → PONG     链路自检
 *
 * 板号与按钮号：
 *   BOARD 'A'（正方）→ A1=正方一辩  A2=正方三辩
 *   BOARD 'B'（反方）→ B1=反方一辩  B2=反方三辩
 *   BOARD '4'（一板四键）→ 41=正方一辩 42=正方三辩 43=反方一辩 44=反方三辩
 *
 * 编译：Arduino IDE 选 ESP32 Dev Module，无需任何第三方库。
 */

#define BOARD        'B'   // ★ 已预置：'A'=正方 / 'B'=反方 / '4'=一块板接四个按钮
#define HAS_BUZZER   0        // 1=接了有源蜂鸣器；0=没有，用板载 LED 提示
#define HAS_LED      1        // 1=用板载 LED（多数 ESP32 开发板是 GPIO2）
const int PIN_BTN[4] = { 4, 5, 19, 21 };   // 按钮1~4 的引脚（一端接这些脚，另一端接 GND）
const int BUZZER = 18;
const int LED    = 2;
const unsigned long DEBOUNCE_MS = 15;
const unsigned long BEAT_MS     = 5000;    // 心跳间隔：让大屏知道板子还活着

struct Btn {
  int pin;
  int idx;             // 按钮号 1~4
  bool lastRead;
  bool state;
  unsigned long tEdge;
};

Btn btns[4];
int btnCount = 0;
unsigned long lastBeat = 0;
String rxLine = "";

void buildButtons() {
  if (BOARD == '4') {                     // 一板四键：正一 正三 反一 反三
    btns[0] = { PIN_BTN[0], 1, HIGH, false, 0 };
    btns[1] = { PIN_BTN[1], 2, HIGH, false, 0 };
    btns[2] = { PIN_BTN[2], 3, HIGH, false, 0 };
    btns[3] = { PIN_BTN[3], 4, HIGH, false, 0 };
    btnCount = 4;
  } else {                                // 两块板各 2 键
    btns[0] = { PIN_BTN[0], 1, HIGH, false, 0 };
    btns[1] = { PIN_BTN[1], 2, HIGH, false, 0 };
    btnCount = 2;
  }
}

void report(int idx, bool pressed) {
  Serial.print(BOARD);
  Serial.print(idx);
  Serial.print(':');
  Serial.println(pressed ? "D" : "U");
}

void buzz(unsigned int ms) {
#if HAS_BUZZER
  digitalWrite(BUZZER, HIGH); delay(ms); digitalWrite(BUZZER, LOW);
#endif
#if HAS_LED
  digitalWrite(LED, LOW); delay(ms); digitalWrite(LED, HIGH);   // 板载 LED 低电平点亮
#endif
  if (!HAS_BUZZER && !HAS_LED) delay(ms);
}
/* AI 说完 / 轮到你了：三次快闪 */
void hint() {
#if HAS_BUZZER
  buzz(120); delay(80); buzz(120);
#endif
#if HAS_LED
  for (int i = 0; i < 3; i++) { digitalWrite(LED, LOW); delay(80); digitalWrite(LED, HIGH); delay(80); }
#endif
}

void setup() {
  Serial.begin(115200);
  delay(300);
  buildButtons();
  for (int i = 0; i < btnCount; i++) pinMode(btns[i].pin, INPUT_PULLUP);
  pinMode(BUZZER, OUTPUT); digitalWrite(BUZZER, LOW);
#if HAS_LED
  pinMode(LED, OUTPUT); digitalWrite(LED, HIGH);
#endif
  /* 上电握手：大屏串口监视里能看到 READY A / READY B，确认板子活着 */
  Serial.print("READY ");
  Serial.println(BOARD);
  hint();
  lastBeat = millis();
}

void handleCommand(String s) {
  s.trim();
  if (!s.length()) return;
  /* 只响应给本板的指令：A:BUZZ 给 A 板、B:BUZZ 给 B 板；不带板号前缀的一律响应 */
  char head = s.charAt(0);
  bool mine = (head == BOARD) || (head != 'A' && head != 'B' && head != '4');
  if (s.endsWith("BUZZ")) {
    if (mine) buzz(120);
  } else if (s.endsWith("HINT")) {          // ★ 大屏在「AI 说完 / 轮到你了」时发 A:HINT
    if (mine) hint();
  } else if (s.endsWith("PING")) {
    Serial.print(BOARD);
    Serial.println(":PONG");
  }
}

void loop() {
  // ---- 按钮去抖 + 上报 ----
  for (int i = 0; i < btnCount; i++) {
    bool raw = digitalRead(btns[i].pin);
    if (raw != btns[i].lastRead) {
      btns[i].lastRead = raw;
      btns[i].tEdge = millis();
    } else if (millis() - btns[i].tEdge >= DEBOUNCE_MS) {
      bool pressed = (raw == LOW);
      if (pressed != btns[i].state) {
        btns[i].state = pressed;
        report(btns[i].idx, pressed);
        if (pressed) buzz(25);              // 按下给一点触感反馈
      }
    }
  }

  // ---- 心跳（便于确认 USB 链路没断）----
  if (millis() - lastBeat >= BEAT_MS) {
    lastBeat = millis();
    Serial.print(BOARD);
    Serial.println(":BEAT");
  }

  // ---- 接收电脑指令 ----
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (rxLine.length()) { handleCommand(rxLine); rxLine = ""; }
    } else if (rxLine.length() < 40) {
      rxLine += c;
    }
  }
}
