#include <SPI.h>        // SPI interface
#include <SdFat.h>     // SD filesystem
#include <NTPClient.h> // NTP client
#include <WiFiUdp.h>   // UDP support
#include <WiFiS3.h>    // WiFi driver

//WiFi
const char* ssid       = "xxxx";      // WiFi SSID
const char* password   = "xxxx";  // WiFi password
const char* serverHost = "192.168.0.12";  // Server IP
const uint16_t serverPort = 5000;         // Server port

//Pins
const int micPin   = A0; // Microphone pin
const int redPin   = 6;  // Red LED
const int greenPin = 3;  // Green LED
const int bluePin  = 5;  // Blue LED

//SD
const int SD_CS_PIN = 10; // SD chip select
SdFat SD;                // SD interface
SdFile logFile;          // Log file
bool sdReady = false;    // SD status

//dB Thresholds 
//  Quiet    : < 35 dB
//  Moderate : 35–55 dB
//  Loud     : > 55 dB

const int QUIET_DB_MAX    = 35; // Quiet limit
const int MODERATE_DB_MAX = 55; // Moderate limit

//State
float smooth = 0.0f;     // Smoothed signal
const float alpha = 0.2f; // Filter factor
bool alarmActive = false; // Alarm latch

//Timing
const unsigned long DEBUG_PRINT_INTERVAL  = 1000; // Debug rate
const unsigned long SERVER_SEND_INTERVAL  = 1000; // Send rate

//Time
WiFiUDP ntpUDP; // UDP object
NTPClient timeClient(ntpUDP, "pool.ntp.org", 0, 60000); // NTP client

//Prototypes
void setRGB(int r, int g, int b);            // Set LED
void logToSD(int dbValue, String location);  // SD logging
int  convertToDB(int rawValue);              // ADC to dB
String getFormattedDate();                   // Date string
void sendToServer(int dbValue, const char* location); // HTTP send

void setup() {
    Serial.begin(9600); // Start serial
    delay(2000);        // Serial settle

    pinMode(redPin, OUTPUT);   // Red output
    pinMode(greenPin, OUTPUT); // Green output
    pinMode(bluePin, OUTPUT);  // Blue output

    // LED self-test ensures wiring and PWM are correct 
    setRGB(255,0,0); delay(300);   // Red test
    setRGB(0,255,0); delay(300);   // Green test
    setRGB(0,0,255); delay(300);   // Blue test
    setRGB(150,255,0); delay(500); // Yellow test
    setRGB(0,0,0);                 // LEDs off

    if (SD.begin(SD_CS_PIN, SPI_HALF_SPEED)) { // Init SD
        sdReady = true;                        // SD ready
        if (!SD.exists("miclog.csv")) {        // File check
            if (logFile.open("miclog.csv", O_WRITE | O_CREAT | O_AT_END)) { // Create file
                logFile.println("Date,Time,Decibels,Location"); // CSV header
                logFile.close();              // Close file
            }
        }
    }

    WiFi.begin(ssid, password); // Start WiFi
    int tries = 0;              // Retry count
    while (WiFi.status() != WL_CONNECTED && tries < 20) { // Wait WiFi
        delay(500);             // Retry delay
        tries++;                // Increment tries
    }

    if (WiFi.status() == WL_CONNECTED) { // WiFi OK
        timeClient.begin();              // Start NTP
        timeClient.update();             // Sync time
    }
}

void loop() {
    timeClient.update();         // Refresh time
    unsigned long now = millis(); // Current time

    int raw = analogRead(micPin); // Read microphone

    // Exponential smoothing removes short spikes 
    smooth = alpha * raw + (1.0f - alpha) * smooth; // Low-pass filter
    int micVal = (int)smooth; // Smoothed value
    int dbVal  = convertToDB(micVal); // Convert dB

    static unsigned long lastPrint = 0; // Print timer
    static unsigned long lastSend  = 0; // Send timer

    // Alarm is latched so it doesn't flicker around thresholds 
    if (!alarmActive && dbVal > MODERATE_DB_MAX) { // Enter loud
        alarmActive = true;                        // Set latch
        logToSD(dbVal, "Room 1");                  // Log event
    }
    else if (alarmActive && dbVal < QUIET_DB_MAX) { // Return quiet
        alarmActive = false;                       // Clear latch
        logToSD(dbVal, "Room 1");                  // Log event
    }

    // LED behavior reflects acoustic category 
    if (dbVal > MODERATE_DB_MAX) { // Loud state
        setRGB(255,0,0); delay(100); // Red flash
        setRGB(0,0,0);   delay(100); // Flash off
    }
    else if (dbVal >= QUIET_DB_MAX) { // Moderate state
        setRGB(255, 120, 0);          // Yellow LED
        delay(50);                    // Stability delay
    }
    else {                            // Quiet state
        setRGB(0,255,0);              // Green LED
        delay(50);                    // Stability delay
    }

    if (now - lastSend >= SERVER_SEND_INTERVAL) { // Send due
        sendToServer(dbVal, "Room 1");             // HTTP post
        lastSend = now;                            // Reset timer
    }

    if (now - lastPrint >= DEBUG_PRINT_INTERVAL) { // Debug due
        Serial.print("Raw: ");                     // Label raw
        Serial.print(micVal);                      // Print raw
        Serial.print(" | dB: ");                   // Label dB
        Serial.print(dbVal);                       // Print dB
        Serial.print(" | State: ");                // Label state
        if (dbVal > MODERATE_DB_MAX) Serial.println("LOUD");     // Loud state
        else if (dbVal >= QUIET_DB_MAX) Serial.println("MODERATE"); // Moderate
        else Serial.println("QUIET");              // Quiet state
        lastPrint = now;                           // Reset timer
    }
}

//Helpers

void setRGB(int r, int g, int b) {
    analogWrite(redPin, r);   // Red PWM
    analogWrite(greenPin, g); // Green PWM
    analogWrite(bluePin, b);  // Blue PWM
}

// Raw ADC values are mapped to a realistic indoor dB range 
int convertToDB(int rawValue) {
    int db = map(rawValue, 340, 1000, 30, 80); // Map range
    return constrain(db, 30, 80);              // Clamp value
}

void logToSD(int dbValue, String location) {
    if (!sdReady) return; // Abort if no SD

    if (logFile.open("miclog.csv", O_WRITE | O_CREAT | O_AT_END)) { // Open file
        logFile.print(getFormattedDate());      // Write date
        logFile.print(",");                     // CSV comma
        logFile.print(timeClient.getFormattedTime()); // Write time
        logFile.print(",");                     // CSV comma
        logFile.print(dbValue);                 // Write dB
        logFile.print(",");                     // CSV comma
        logFile.println(location);              // Write location
        logFile.close();                        // Close file
    }
}

String getFormattedDate() {
    time_t t = timeClient.getEpochTime(); // Get epoch
    if (t < 100000) return "01/01/2000";  // Fallback date

    struct tm *tmr = localtime(&t); // Convert time
    return String(tmr->tm_mday) + "/" +     // Day
           String(tmr->tm_mon + 1) + "/" +  // Month
           String(tmr->tm_year + 1900);     // Year
}

void sendToServer(int dbValue, const char* location) {
    if (WiFi.status() != WL_CONNECTED) return; // Abort if offline

    WiFiClient client; // Client object
    if (!client.connect(serverHost, serverPort)) return; // Connect server

    String payload = String("{\"date\":\"") + getFormattedDate() + // JSON date
                     "\",\"time\":\"" + timeClient.getFormattedTime() + // JSON time
                     "\",\"decibels\":" + dbValue + // JSON dB
                     ",\"location\":\"" + location + "\"}"; // JSON location

    client.println("POST /ingest HTTP/1.1"); // HTTP request
    client.print("Host: "); client.println(serverHost); // Host header
    client.println("Content-Type: application/json"); // JSON type
    client.print("Content-Length: "); client.println(payload.length()); // Length header
    client.println("Connection: close"); // Close request
    client.println();                    // Header end
    client.print(payload);               // Send payload

    client.stop(); // Close socket
}
