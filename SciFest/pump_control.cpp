#include <gpiod.h>
#include <chrono>
#include <csignal>
#include <cstring>
#include <iostream>
#include <thread>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <unistd.h>
#include <errno.h>
#include <fcntl.h>
#include <sys/select.h>
#include <algorithm>

// You used "BCM pin numbers" in v1. In libgpiod v2 we request by *line offsets* on a gpiochip.
// On Raspberry Pi these often match BCM numbers on /dev/gpiochip0, but do not assume.
// Verify with: gpioinfo /dev/gpiochip0  (or use gpiofind).
static constexpr unsigned PUMP_OFFSET = 20; // main solenoid valve
static constexpr unsigned VENT_OFFSET = 21; // vent/bleed valve
static const char* CHIP_PATH = "/dev/gpiochip0";

static const char* SERVER_IP = "192.168.0.37";
static constexpr int SERVER_PORT = 8888;

// Active-low semantics: logical 1 => assert (drive pin LOW)
enum class ValveState { On, Off };
enum class State { WAITING, DELIVERING };

static volatile bool keep_running = true;
void handle_sigint(int) { keep_running = false; }

static inline void sleep_ms(int ms) { std::this_thread::sleep_for(std::chrono::milliseconds(ms)); }
static inline void sleep_s (int s ) { std::this_thread::sleep_for(std::chrono::seconds(s)); }

// v2: you set values on a gpiod_line_request*, addressed by offset
static inline void set_valve(gpiod_line_request* req, unsigned offset, ValveState state) {
    const gpiod_line_value v =
        (state == ValveState::On) ? GPIOD_LINE_VALUE_ACTIVE : GPIOD_LINE_VALUE_INACTIVE;

    if (gpiod_line_request_set_value(req, offset, v) < 0) {
        std::perror("gpiod_line_request_set_value");
    }
}

int main() {
    std::signal(SIGINT, handle_sigint);

    // ---- libgpiod v2 setup ----
    gpiod_chip* chip = gpiod_chip_open(CHIP_PATH);
    if (!chip) { std::perror("gpiod_chip_open"); return 1; }

    gpiod_line_settings* settings = gpiod_line_settings_new();
    if (!settings) { std::perror("gpiod_line_settings_new"); gpiod_chip_close(chip); return 1; }

    gpiod_line_settings_set_direction(settings, GPIOD_LINE_DIRECTION_OUTPUT);

    // Match your v1 semantics: ACTIVE_LOW flag.
    // With active-low set, "value=1" means "active" => physical LOW.
    gpiod_line_settings_set_active_low(settings, true);

    gpiod_line_config* lcfg = gpiod_line_config_new();
    if (!lcfg) {
        std::perror("gpiod_line_config_new");
        gpiod_line_settings_free(settings);
        gpiod_chip_close(chip);
        return 1;
    }

    {
        unsigned offs1 = PUMP_OFFSET;
        unsigned offs2 = VENT_OFFSET;
        if (gpiod_line_config_add_line_settings(lcfg, &offs1, 1, settings) < 0) {
            std::perror("gpiod_line_config_add_line_settings(pump)");
            gpiod_line_config_free(lcfg);
            gpiod_line_settings_free(settings);
            gpiod_chip_close(chip);
            return 1;
        }
        if (gpiod_line_config_add_line_settings(lcfg, &offs2, 1, settings) < 0) {
            std::perror("gpiod_line_config_add_line_settings(vent)");
            gpiod_line_config_free(lcfg);
            gpiod_line_settings_free(settings);
            gpiod_chip_close(chip);
            return 1;
        }
    }

    gpiod_request_config* rcfg = gpiod_request_config_new();
    if (!rcfg) {
        std::perror("gpiod_request_config_new");
        gpiod_line_config_free(lcfg);
        gpiod_line_settings_free(settings);
        gpiod_chip_close(chip);
        return 1;
    }
    gpiod_request_config_set_consumer(rcfg, "pump_kb");

    gpiod_line_request* req = gpiod_chip_request_lines(chip, rcfg, lcfg);
    if (!req) {
        std::perror("gpiod_chip_request_lines");
        gpiod_request_config_free(rcfg);
        gpiod_line_config_free(lcfg);
        gpiod_line_settings_free(settings);
        gpiod_chip_close(chip);
        return 1;
    }

    // Start OFF: pump off, vent closed
    set_valve(req, PUMP_OFFSET, ValveState::Off);
    set_valve(req, VENT_OFFSET, ValveState::Off);

    // ---- Socket setup - connect to server ----
    int sockfd;
    struct sockaddr_in serv_addr;
    memset(&serv_addr, 0, sizeof(serv_addr));
    serv_addr.sin_family = AF_INET;
    serv_addr.sin_port = htons(SERVER_PORT);
    if (inet_pton(AF_INET, SERVER_IP, &serv_addr.sin_addr) <= 0) {
        std::perror("inet_pton");
        gpiod_line_request_release(req);
        gpiod_request_config_free(rcfg);
        gpiod_line_config_free(lcfg);
        gpiod_line_settings_free(settings);
        gpiod_chip_close(chip);
        return 1;
    }

    std::cout << "Attempting to connect to server at " << SERVER_IP << ":" << SERVER_PORT << "\n";
    while (keep_running) {
        sockfd = socket(AF_INET, SOCK_STREAM, 0);
        if (sockfd < 0) { std::perror("socket"); break; }

        fcntl(sockfd, F_SETFL, O_NONBLOCK);
        int res = connect(sockfd, (struct sockaddr*)&serv_addr, sizeof(serv_addr));
        if (res == 0) {
            std::cout << "Connected to server at " << SERVER_IP << ":" << SERVER_PORT << "\n";
            break;
        } else if (errno == EINPROGRESS) {
            fd_set writefds;
            FD_ZERO(&writefds);
            FD_SET(sockfd, &writefds);
            struct timeval tv = {5, 0};
            int retval = select(sockfd + 1, NULL, &writefds, NULL, &tv);
            if (retval > 0) {
                int error;
                socklen_t len = sizeof(error);
                getsockopt(sockfd, SOL_SOCKET, SO_ERROR, &error, &len);
                if (error == 0) {
                    std::cout << "Connected to server at " << SERVER_IP << ":" << SERVER_PORT << "\n";
                    break;
                }
            }
        }

        std::cout << "Connect failed, retrying in 5 seconds\n";
        close(sockfd);
        sleep_s(5);
    }

    if (!keep_running) {
        std::cout << "Exiting due to signal during connection attempt.\n";
        // safe shutdown of GPIO
        set_valve(req, PUMP_OFFSET, ValveState::Off);
        set_valve(req, VENT_OFFSET, ValveState::Off);
        gpiod_line_request_release(req);
        gpiod_request_config_free(rcfg);
        gpiod_line_config_free(lcfg);
        gpiod_line_settings_free(settings);
        gpiod_chip_close(chip);
        return 1;
    }

    if (!keep_running) return 1;

    std::cout << "Waiting for commands: 'pickup reached' to turn pump ON, 'drop reached' to turn pump OFF\n";

    auto pump_on = [&]() {
        set_valve(req, PUMP_OFFSET, ValveState::On); // energize main solenoid
        std::cout << "[STATE] Pump ON\n";
    };

    auto pump_off = [&]() {
        set_valve(req, PUMP_OFFSET, ValveState::Off); // stop main solenoid
        sleep_ms(50);
        set_valve(req, VENT_OFFSET, ValveState::On);  // open vent
        sleep_s(1);
        set_valve(req, VENT_OFFSET, ValveState::Off); // close vent
        sleep_ms(50);
        std::cout << "[STATE] Pump OFF (vented)\n";
    };

    bool is_on = false; // start OFF
    std::cout << "Current: OFF\n";

    State state = State::WAITING;
    int send_counter = 0;
    std::cout << "Waiting for human input (press Enter to start delivering sticker)\n";

    auto reconnect = [&]() -> bool {
        while (keep_running) {
            sockfd = socket(AF_INET, SOCK_STREAM, 0);
            if (sockfd < 0) { std::perror("socket"); return false; }

            fcntl(sockfd, F_SETFL, O_NONBLOCK);
            int res = connect(sockfd, (struct sockaddr*)&serv_addr, sizeof(serv_addr));
            if (res == 0) {
                std::cout << "Reconnected to server at " << SERVER_IP << ":" << SERVER_PORT << "\n";
                state = State::WAITING;
                send_counter = 0;
                return true;
            } else if (errno == EINPROGRESS) {
                fd_set writefds;
                FD_ZERO(&writefds);
                FD_SET(sockfd, &writefds);
                struct timeval tv = {5, 0};
                int retval = select(sockfd + 1, NULL, &writefds, NULL, &tv);
                if (retval > 0) {
                    int error;
                    socklen_t len = sizeof(error);
                    getsockopt(sockfd, SOL_SOCKET, SO_ERROR, &error, &len);
                    if (error == 0) {
                        std::cout << "Reconnected to server at " << SERVER_IP << ":" << SERVER_PORT << "\n";
                        state = State::WAITING;
                        send_counter = 0;
                        return true;
                    }
                }
            }
            std::cout << "Reconnect failed, retrying in 5 seconds\n";
            close(sockfd);
            sleep_s(5);
        }
        return false;
    };

    while (keep_running) {
        fd_set readfds;
        FD_ZERO(&readfds);
        FD_SET(sockfd, &readfds);
        FD_SET(0, &readfds); // stdin
        struct timeval tv = {1, 0};
        int maxfd = std::max(sockfd, 0) + 1;

        int retval = select(maxfd, &readfds, NULL, NULL, &tv);
        if (retval == -1) {
            if (errno == EINTR) continue;
            std::perror("select, reconnecting");
            close(sockfd);
            if (!reconnect()) break;
            continue;
        } else if (retval == 0) {
            // timeout
            if (state == State::WAITING) {
                send_counter++;
                if (send_counter >= 10) {
                    const char* msg = "wait until next sticker\n";
                    (void)write(sockfd, msg, strlen(msg));
                    std::cout << "Sent: wait until next sticker\n";
                    send_counter = 0;
                }
            }
        } else {
            if (FD_ISSET(0, &readfds)) {
                char c;
                read(0, &c, 1);
                if (c == '\n' && state == State::WAITING) {
                    const char* msg = "deliver a new sticker\n";
                    (void)write(sockfd, msg, strlen(msg));
                    state = State::DELIVERING;
                    std::cout << "Started delivering sticker\n";
                }
            }

            if (FD_ISSET(sockfd, &readfds)) {
                char buffer[256];
                memset(buffer, 0, sizeof(buffer));
                int n = read(sockfd, buffer, sizeof(buffer) - 1);

                if (n <= 0) {
                    if (n == 0) std::cout << "Server closed connection, reconnecting...\n";
                    else std::perror("read, reconnecting");
                    close(sockfd);
                    if (!reconnect()) break;
                    continue;
                }

                std::string cmd(buffer);
                std::cout << "Received: " << cmd << std::endl;

                if (state == State::DELIVERING) {
                    if (cmd.find("pickup reached") != std::string::npos) {
                        if (!is_on) { pump_on(); is_on = true; }
                    } else if (cmd.find("drop reached") != std::string::npos) {
                        if (is_on) { pump_off(); is_on = false; }
                    } else if (cmd.find("one sticker finished") != std::string::npos) {
                        state = State::WAITING;
                        std::cout << "Sticker finished, waiting for next\n";
                    }
                }
            }
        }
    }

    // Close socket
    close(sockfd);

    // Ensure safe shutdown state
    if (is_on) {
        pump_off();
        is_on = false;
    } else {
        set_valve(req, PUMP_OFFSET, ValveState::Off);
        set_valve(req, VENT_OFFSET, ValveState::Off);
    }

    // ---- libgpiod v2 cleanup ----
    gpiod_line_request_release(req);
    gpiod_request_config_free(rcfg);
    gpiod_line_config_free(lcfg);
    gpiod_line_settings_free(settings);
    gpiod_chip_close(chip);

    std::cout << "Exited.\n";
    return 0;
}
