/*
 * 人机辩论赛 · 抢话按钮（USB 串口版）· 两块板通用
 * ============================================================
 * 两块板烧同一份程序，只改下面一行 #define BOARD：
 *   正方那块 → #define BOARD 'A'
 *   反方那块 → #define BOARD 'B'
 *   （想用一块板接四个按钮 → #define BOARD '4'）
 *
 * 接线（沿用你的引脚，全部避开 C3 的 USB 差分线）：
 *   BTN1 = GPIO3    BTN2 = GPIO4    BTN3 = GPIO5    BTN4 = GPIO10
 *   每个按钮：一端接该 GPIO，另一端接 GND（内部上拉，按下读到 LOW）
 *
 * ★ 为什么绝对不能用 GPIO18：
 *   ESP32-C3 的 GPIO18 = USB D-，GPIO19 = USB D+。
 *   一旦 pinMode(18, OUTPUT) 并拉低，USB 立刻断掉，
 *   症状就是「按住 BOOT 能连、一按 RST 电脑里就找不到板子」。
 * ★ GPIO9 是 BOOT 键：做按钮也能读，但按下时再复位会进下载模式，不建议。
 *
 * 与大屏网页的协议（115200，每行一条，\n 结尾）：
 *   板上报： A1:D / A1:U      板号+按钮号 : 按下 / 松开
 *            READY A          上电握手
 *            A:BEAT           每 5 秒心跳（确认链路活着）
 *            A:PONG           回应电脑的 A:PING
 *   电脑发： A:BUZZ / A:HINT   （本板无蜂鸣器/LED，忽略即可）
 *
 * 用法：
 *   1) 打开 https://120.48.43.211/debate/time/ （必须 https，Web Serial 才可用）
 *   2) 点「连接开发板（可连两块）」，选第一块 → 再点一次选第二块
 *   3) 按下按钮，大屏对应辩位的灯就会亮
 *
 * 编译：开发板选 "ESP32C3 Dev Module"，USB CDC On Boot = Enabled
 */

#define BOARD        'A'    // ★ 正方填 'A'，反方填 'B'，一板四键填 '4'
#define HAS_BUZZER   0      // 1=接了蜂鸣器（接 GPIO0 或 1，别用 18/19）
#define HAS_LED      0      // C3 各板 LED 引脚不同，默认关；确认自己板子的脚后再开

const int BTN[4] = { 3, 4, 5, 10 };     // 沿用你的接线；第 5 个 GPIO9 是 BOOT 键，未纳入
const unsigned long DEBOUNCE_MS = 15;
const unsigned long BEAT_MS     = 5000;

const int btnCount = (BOARD == '4') ? 4 : 2;
bool lastRead[4];
bool stable[4];
unsigned long tEdge[4];
unsigned long lastBeat = 0;
String rxLine = "";

void report(int i, bool down) {
  Serial.print(BOARD);
  Serial.print(i + 1);
  Serial.print(':');
  Serial.println(down ? "D" : "U");
}

void setup() {
  Serial.begin(115200);
  delay(1200);                    // 等 USB 枚举完成，避免开头几行握手丢失

  for (int i = 0; i < btnCount; i++) {
    pinMode(BTN[i], INPUT_PULLUP);   // INPUT_PULLUP，按下接 GND → LOW
    lastRead[i] = HIGH;
    stable[i]   = false;
    tEdge[i]    = 0;
  }

  Serial.print("READY ");
  Serial.println(BOARD);
  lastBeat = millis();
}

void handleCommand(String s) {
  s.trim();
  if (!s.length()) return;

  if (s.endsWith("PING")) {              // 链路自检：任何板都回
    Serial.print(BOARD);
    Serial.println(":PONG");
    return;
  }

  char head = s.charAt(0);
  bool mine = (head == BOARD) || (head != 'A' && head != 'B' && head != '4');
  if (!mine) return;

  /* 本板没有蜂鸣器 / 板载 LED，BUZZ、HINT 收到后无需动作 */
}

void loop() {
  /* ---- 按钮去抖 + 上报 ---- */
  for (int i = 0; i < btnCount; i++) {
    bool raw = digitalRead(BTN[i]);
    if (raw != lastRead[i]) {
      lastRead[i] = raw;
      tEdge[i] = millis();
    } else if (millis() - tEdge[i] >= DEBOUNCE_MS) {
      bool pressed = (raw == LOW);
      if (pressed != stable[i]) {
        stable[i] = pressed;
        report(i, pressed);
      }
    }
  }

  /* ---- 心跳：大屏据此确认板子还活着 ---- */
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
