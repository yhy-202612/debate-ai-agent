/*
 * 人机辩论赛 · 抢话按钮（ESP32 / Arduino）
 * ------------------------------------------------------------
 * 硬件只有「按钮」——灯全部由电脑上的大屏程序绘制，本板不再接灯。
 *
 * 两块板烧同一份程序，只改下面的 BOARD：
 *   正方侧（A 板）: #define BOARD 'A'
 *   反方侧（B 板）: #define BOARD 'B'
 *
 * 接线（默认引脚，可按需改）：
 *   按钮1  —— GPIO 4   —— 另一端接 GND
 *   按钮2  —— GPIO 5   —— 另一端接 GND
 *   蜂鸣器 —— GPIO 18  —— 可选；没有蜂鸣器就置 HAS_BUZZER 0，改用板载 LED 提示
 *   （用 INPUT_PULLUP，按下读到 LOW）
 *
 * 通信协议（115200，USB 串口，每行一条，\n 结尾）：
 *   板上报： A1:D  / A1:U      （板号+按钮号 : D按下 / U松开）
 *   电脑下发：A:BUZZ            （蜂鸣器短鸣一声）
 *            A:PING  → 板回 PONG（可选，用于测链路）
 */

#define BOARD        'A'      // ★ 正方侧填 'A'，反方侧填 'B'
#define HAS_BUZZER   0        // 1=接了有源蜂鸣器；0=没有，用开发板自带 LED 提示
#define HAS_LED      1        // 1=用板载 LED（多数 ESP32 开发板是 GPIO2）
const int BTN1   = 4;
const int BTN2   = 5;
const int BUZZER = 18;
const int LED    = 2;
const unsigned long DEBOUNCE_MS = 15;

struct Btn {
  int pin;
  int idx;
  bool lastRead;       // 上次稳定电平（HIGH=未按下）
  bool state;          // 当前稳定状态（true=按下）
  unsigned long tEdge; // 上次电平变化时刻
};

Btn btns[2] = {
  { BTN1, 1, HIGH, false, 0 },
  { BTN2, 2, HIGH, false, 0 },
};

String rxLine = "";

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

void setup() {
  Serial.begin(115200);
  delay(300);
  pinMode(BTN1, INPUT_PULLUP);
  pinMode(BTN2, INPUT_PULLUP);
  pinMode(BUZZER, OUTPUT);
  digitalWrite(BUZZER, LOW);
#if HAS_LED
  pinMode(LED, OUTPUT); digitalWrite(LED, HIGH);
#endif
  Serial.print("READY ");
  Serial.println(BOARD);
}

void loop() {
  // ---- 按钮去抖 ----
  for (int i = 0; i < 2; i++) {
    bool raw = digitalRead(btns[i].pin);
    if (raw != btns[i].lastRead) {
      btns[i].lastRead = raw;
      btns[i].tEdge = millis();
    } else if (millis() - btns[i].tEdge >= DEBOUNCE_MS) {
      bool pressed = (raw == LOW);
      if (pressed != btns[i].state) {
        btns[i].state = pressed;
        report(btns[i].idx, pressed);
      }
    }
  }

  // ---- 接收电脑指令 ----
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (rxLine.length()) {
        handleCommand(rxLine);
        rxLine = "";
      }
    } else if (rxLine.length() < 40) {
      rxLine += c;
    }
  }
}

void handleCommand(String s) {
  s.trim();
  // 只响属于本板的蜂鸣：A:BUZZ 给 A 板，B:BUZZ 给 B 板；不带前缀也响
  if (s.endsWith("BUZZ")) {
    char head = s.charAt(0);
    if (head == BOARD || head == 'C') buzz(120);
  } else if (s.endsWith("PING")) {
    Serial.print(BOARD);
    Serial.println(":PONG");
  }
}
