#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

/* Encoded flag bytes -- decoded only inside win(). Not reachable by
 * cat/find/grep/strings/xxd on the binary or the mounted directory. Only
 * an exploit that reaches win() ever sees the plaintext. */
static const unsigned char ENC[] = {
    0x3c,0x36,0x3b,0x3d,0x21,0x38,0x3b,0x29,0x3f,0x36,0x33,
    0x34,0x3f,0x05,0x29,0x37,0x35,0x31,0x3f,0x05,0x35,0x31,0x27
};

void win(void) {
    char buf[sizeof(ENC) + 1];
    for (size_t i = 0; i < sizeof(ENC); i++) buf[i] = ENC[i] ^ 0x5a;
    buf[sizeof(ENC)] = '\0';
    puts(buf);
}

void vuln(void) {
    char buf[64];
    puts("say something:");
    read(0, buf, 200);
    printf("hi %s\n", buf);
}

int main(void) {
    setvbuf(stdout, NULL, _IONBF, 0);
    vuln();
    return 0;
}
