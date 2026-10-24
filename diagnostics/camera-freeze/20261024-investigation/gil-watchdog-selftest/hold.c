#include <time.h>
void hold_gil(void) {
    struct timespec start, now, pause = {0, 20000000};
    clock_gettime(CLOCK_MONOTONIC, &start);
    do {
        nanosleep(&pause, 0);
        clock_gettime(CLOCK_MONOTONIC, &now);
    } while (now.tv_sec - start.tv_sec < 5);
}
