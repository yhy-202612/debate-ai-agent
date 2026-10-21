/*
 * 人机辩论赛 · 抢话按钮（USB 串口版）· ESP32-C3 安全版
 * ============================================================
 * 数据线连电脑 → 浏览器（大屏页面）通过 Web Serial 直接读取按钮事件
 *   ESP32 只做「中转」：按钮 → 串口 → 大屏网页 → 亮灯/计时/抢话仲裁 → （可选）转发给智能体
 *   零网络依赖，实测约 0.1 秒，正式比赛首选这个版本。
 *
 * ★★★ 本版修正了 ESP32-C3 的致命引脚冲突 ★★★
 *   C3 的 GPIO18 = USB D-，GPIO19 = USB D+，是芯片原生 USB 的差分线。
 *   旧版把 GPIO18 当蜂鸣器并 pinMode(OUTPUT) + digitalWrite(LOW)，会直接把 USB 掐断：
 *   表现就是「下载模式能连、按 RST 后电脑里板子彻底消失、串口监视器空白」。
 *   本版把蜂鸣器移到 GPIO10，按钮移到 4/5/6/7，并加了编译期防线：一旦改回 18/19 直接报错。
 *
 * ★ 本文件已预置为「正方 侧」那块板（BOARD 'A'），按钮对应：正方一辩（GPIO 4）、正方三辩（GPIO 5）
 *
 * 接线（ESP32-C3 推荐引脚）：
 *   按钮1  —— GPIO 4   —— 另一端接 GND
 *   按钮2  —— GPIO 5   —— 另一端接 GND
 *   按钮3  —— GPIO 6   —— 另一端接 GND   （仅 BOARD '4' 用）
 *   按钮4  —— GPIO 7   —— 另一端接 GND   （仅 BOARD '4' 用）
 *   蜂鸣器 —— GPIO 10  —— 有源蜂鸣器正极接 10、负极接 GND（可选，没接就保持 HAS_BUZZER 0）
 *   （INPUT_PULLUP，按下读到 LOW，不需要电阻）
 *
 * ★ C3 引脚禁区（务必避开，接了就会出各种怪问题）：
 *   GPIO18 / GPIO19   → USB D- / D+（占用即断联）
 *   GPIO20 / GPIO21   → UART0 RX / TX（串口日志）
 *   GPIO8  / GPIO9    → 启动 strapping / BOOT 按键
 *   GPIO11 ~ GPIO17   → 片上 Flash，绝对不能碰
 *
 * 通信协议（115200，每行一条，\n 结尾）：
 *   板上报： A1:D / A1:U      板号+按钮号 : D按下 / U松开
 *            READY A          上电握手（大屏可据此确认板子活着）
 *            A:BEAT           每 5 秒心跳（确认 USB 链路没断）
 *            A:PONG           回应电脑的 A:PING
 *   电脑下发：A:BUZZ            蜂鸣提示
 *            A:HINT            「轮到你了」三闪
 *            A:PING → PONG     链路自检
 *
 * 板号与按钮号：
 *   BOARD 'A'（正方）→ A1=正方一辩  A2=正方三辩
 *   BOARD 'B'（反方）→ B1=反方一辩  B2=反方三辩
 *   BOARD '4'（一板四键）→ 41=正方一辩 42=正方三辩 43=反方一辩 44=反方三辩
 *
 * 编译：Arduino IDE → 开发板选 "ESP32C3 Dev Module"
 *       → USB CDC On Boot = Enabled → 端口选板子的 COM
 */

#define BOARD        'A'   // ★ 已预置：'A'=正方 / 'B'=反方 / '4'=一块板接四个按钮
#define HAS_BUZZER   0     // 1=接了有源蜂鸣器（接 GPIO10）；0=没接
#define HAS_LED      0     // C3 各板 LED 引脚不统一（GPIO8 / 2 / 12 都有），默认关；
                           // 确认自己板子的 LED 脚后再改成 1，并把 PIN_LED 改成那个脚

/* ---------------- 引脚定义 ---------------- */
#define PIN_BTN1     4
#define PIN_BTN2     5
#define PIN_BTN3     6
#define PIN_BTN4     7
#define PIN_BUZZER   10
#define PIN_LED      2

/* 编译期防线：C3 上把 USB 差分线当外设用，直接编译失败，免得再踩一次 */
#if defined(CONFIG_IDF_TARGET_ESP32C3)
  #if (PIN_BTN1 == 18) || (PIN_BTN2 == 18) || (PIN_BTN3 == 18) || (PIN_BTN4 == 18) || (PIN_BUZZER == 18)
    #error "ESP32-C3: GPIO18 是 USB D-，不能接外设！蜂鸣器请用 GPIO10"
  #endif
  #if (PIN_BTN1 == 19) || (PIN_BTN2 == 19) || (PIN_BTN3 == 19) || (PIN_BTN4 == 19) || (PIN_BUZZER == 19)
    #error "ESP32-C3: GPIO19 是 USB D+，不能接外设！请改用 GPIO6 / GPIO7"
  #endif
  #if (PIN_BTN1 >= 11 && PIN_BTN1 <= 17) || (PIN_BTN2 >= 11 && PIN_BTN2 <= 17) || \
      (PIN_BTN3 >= 11 && PIN_BTN3 <= 17) || (PIN_BTN4 >= 11 && PIN_BTN4 <= 17)
    #error "ESP32-C3: GPIO11~17 是片上 Flash 引脚，不能接外设"
  #endif
#endif

const int PIN_BTN[4] = { PIN_BTN1, PIN_BTN2, PIN_BTN3, PIN_BTN4 };
const int BUZZER = PIN_BUZZER;
const int LED    = PIN_LED;

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
  /* 等 USB 枚举，最多 1.2 秒；UART 模式下 !Serial 恒为 false，不会卡住 */
  unsigned long t0 = millis();
  while (!Serial && millis() - t0 < 1200) delay(10);

  buildButtons();
  for (int i = 0; i < btnCount; i++) pinMode(btns[i].pin, INPUT_PULLUP);

#if HAS_BUZZER
  pinMode(BUZZER, OUTPUT); digitalWrite(BUZZER, LOW);
#endif
#if HAS_LED
  pinMode(LED, OUTPUT); digitalWrite(LED, HIGH);
#endif

  /* 上电握手：串口监视器里能看到 READY A，确认板子活着 */
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
  } else if (s.endsWith("HINT")) {          // 大屏在「AI 说完 / 轮到你了」时发 A:HINT
    if (mine) hint();
  } else if (s.endsWith("PING")) {
    Serial.print(BOARD);
    Serial.println(":PONG");
  }
}

void loop() {
  /* ---- 按钮去抖 + 上报 ---- */
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

  /* ---- 心跳（便于确认 USB 链路没断）---- */
  if (millis() - lastBeat >= BEAT_MS) {
    lastBeat = millis();
    Serial.print(BOARD);
    Serial.println(":BEAT");
  }

  /* ---- 接收电脑指令 ---- */
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (rxLine.length()) { handleCommand(rxLine); rxLine = ""; }
    } else if (rxLine.length() < 40) {
      rxLine += c;
    }
  }
}
