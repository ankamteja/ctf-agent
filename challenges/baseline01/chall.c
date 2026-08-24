#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

void win(void) {
    char buf[256];
    FILE *f = fopen("flag.txt", "r");
    if (!f) { puts("flag.txt missing"); exit(1); }
    fgets(buf, sizeof(buf), f);
    printf("%s", buf);
    fclose(f);
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
