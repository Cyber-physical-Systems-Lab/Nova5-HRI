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

// BCM pin numbers
static constexpr unsigned PUMP_PIN = 20; // main solenoid valve
static constexpr unsigned VENT_PIN = 21; // vent/bleed valve
static const char* CHIP_NAME = "gpiochip0";
static const char* SERVER_IP = "192.168.0.37";
static constexpr int SERVER_PORT = 8888;

// Active-low semantics: logical 1 => assert (drive pin LOW)
enum class ValveState { On, Off };
enum class State { WAITING, DELIVERING };

static volatile bool keep_running = true;
void handle_sigint(int) { keep_running = false; }

void set_valve(gpiod_line* line, ValveState state) {
    int value = (state == ValveState::On) ? 1 : 0; // 1 asserts active-low
    if (gpiod_line_set_value(line, value) < 0) std::perror("gpiod_line_set_value");
}

void sleep_ms(int ms) { std::this_thread::sleep_for(std::chrono::milliseconds(ms)); }
void sleep_s (int s ) { std::this_thread::sleep_for(std::chrono::seconds(s)); }

int main() {
    std::signal(SIGINT, handle_sigint);

    // Open chip and lines
    gpiod_chip* chip = gpiod_chip_open_by_name(CHIP_NAME);
    if (!chip) { std::perror("gpiod_chip_open_by_name"); return 1; }

    gpiod_line* pump = gpiod_chip_get_line(chip, PUMP_PIN);
    gpiod_line* vent = gpiod_chip_get_line(chip, VENT_PIN);
    if (!pump || !vent) {
        std::cerr << "Failed to get lines " << PUMP_PIN << " or " << VENT_PIN << "\n";
        gpiod_chip_close(chip);
        return 1;
    }

    // Request outputs with ACTIVE_LOW to match wiring
    gpiod_line_request_config cfg = {};
    cfg.request_type = GPIOD_LINE_REQUEST_DIRECTION_OUTPUT;
    cfg.flags = GPIOD_LINE_REQUEST_FLAG_ACTIVE_LOW;

    cfg.consumer = "pump_kb_pump";
    if (gpiod_line_request(pump, &cfg, 0) < 0) {
        std::perror("gpiod_line_request(pump)");
        gpiod_chip_close(chip);
        return 1;
    }
    cfg.consumer = "pump_kb_vent";
    if (gpiod_line_request(vent, &cfg, 0) < 0) {
        std::perror("gpiod_line_request(vent)");
        gpiod_line_release(pump);
        gpiod_chip_close(chip);
        return 1;
    }

    // Start OFF: pump off, vent closed
    set_valve(pump, ValveState::Off);
    set_valve(vent, ValveState::Off);

    // Socket setup - connect to server
    int sockfd;
    struct sockaddr_in serv_addr;
    memset(&serv_addr, 0, sizeof(serv_addr));
    serv_addr.sin_family = AF_INET;
    serv_addr.sin_port = htons(SERVER_PORT);
    if (inet_pton(AF_INET, SERVER_IP, &serv_addr.sin_addr) <= 0) {
        std::perror("inet_pton"); return 1;
    }
    std::cout << "Attempting to connect to server at " << SERVER_IP << ":" << SERVER_PORT << "\n";
    while (keep_running) {
        sockfd = socket(AF_INET, SOCK_STREAM, 0);
        if (sockfd < 0) { std::perror("socket"); return 1; }
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
        sleep(5);
    }
    if (!keep_running) {
        std::cout << "Exiting due to signal during connection attempt.\n";
        return 1;
    }
    std::cout << "Waiting for commands: 'pickup reached' to turn pump ON, 'drop reached' to turn pump OFF\n";

    auto pump_on = [&]() {
        set_valve(pump, ValveState::On); // energize main solenoid
        std::cout << "[STATE] Pump ON\n";
    };

    auto pump_off = [&]() {
        set_valve(pump, ValveState::Off); // stop main solenoid
        sleep_ms(50);
        set_valve(vent, ValveState::On);  // open vent
        sleep_s(1);
        set_valve(vent, ValveState::Off); // close vent
        sleep_ms(50);
        std::cout << "[STATE] Pump OFF (vented)\n";
    };

    bool is_on = false; // start OFF
    std::cout << "Current: OFF\n";

    State state = State::WAITING;
    int send_counter = 0;
    std::cout << "Waiting for human input (press Enter to start delivering sticker)\n";

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
            // Reconnect loop
            while (keep_running) {
                sockfd = socket(AF_INET, SOCK_STREAM, 0);
                if (sockfd < 0) { std::perror("socket"); return 1; }
                fcntl(sockfd, F_SETFL, O_NONBLOCK);
                int res = connect(sockfd, (struct sockaddr*)&serv_addr, sizeof(serv_addr));
                if (res == 0) {
                    std::cout << "Reconnected to server at " << SERVER_IP << ":" << SERVER_PORT << "\n";
                    state = State::WAITING;
                    send_counter = 0;
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
                            std::cout << "Reconnected to server at " << SERVER_IP << ":" << SERVER_PORT << "\n";
                            state = State::WAITING;
                            send_counter = 0;
                            break;
                        }
                    }
                }
                std::cout << "Reconnect failed, retrying in 5 seconds\n";
                close(sockfd);
                sleep(5);
            }
            continue;
        } else if (retval == 0) {
            // timeout
            if (state == State::WAITING) {
                send_counter++;
                if (send_counter >= 10) {
                    const char* msg = "wait until next sticker\n";
                    write(sockfd, msg, strlen(msg));
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
                    write(sockfd, msg, strlen(msg));
                    state = State::DELIVERING;
                    std::cout << "Started delivering sticker\n";
                }
            }
            if (FD_ISSET(sockfd, &readfds)) {
                char buffer[256];
                memset(buffer, 0, sizeof(buffer));
                int n = read(sockfd, buffer, sizeof(buffer) - 1);
                if (n <= 0) {
                    if (n == 0) {
                        std::cout << "Server closed connection, reconnecting...\n";
                    } else {
                        std::perror("read, reconnecting");
                    }
                    close(sockfd);
                    // Reconnect loop
                    while (keep_running) {
                        sockfd = socket(AF_INET, SOCK_STREAM, 0);
                        if (sockfd < 0) { std::perror("socket"); return 1; }
                        fcntl(sockfd, F_SETFL, O_NONBLOCK);
                        int res = connect(sockfd, (struct sockaddr*)&serv_addr, sizeof(serv_addr));
                        if (res == 0) {
                            std::cout << "Reconnected to server at " << SERVER_IP << ":" << SERVER_PORT << "\n";
                            state = State::WAITING;
                            send_counter = 0;
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
                                    std::cout << "Reconnected to server at " << SERVER_IP << ":" << SERVER_PORT << "\n";
                                    state = State::WAITING;
                                    send_counter = 0;
                                    break;
                                }
                            }
                        }
                        std::cout << "Reconnect failed, retrying in 5 seconds\n";
                        close(sockfd);
                        sleep(5);
                    }
                    continue;
                }
                std::string cmd(buffer);
                std::cout << "Received: " << cmd << std::endl;
                if (state == State::DELIVERING) {
                    if (cmd.find("pickup reached") != std::string::npos) {
                        if (!is_on) {
                            pump_on();
                            is_on = true;
                        }
                    } else if (cmd.find("drop reached") != std::string::npos) {
                        if (is_on) {
                            pump_off();
                            is_on = false;
                        }
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
        set_valve(pump, ValveState::Off);
        set_valve(vent, ValveState::Off);
    }

    gpiod_line_release(vent);
    gpiod_line_release(pump);
    gpiod_chip_close(chip);
    std::cout << "Exited.\n";
    return 0;
}

