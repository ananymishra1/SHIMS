/*
 * Shims Edge — reference C runtime for the Pentium-II class profile.
 *
 * Reads a tiny custom binary format produced by `shims_chem.edge.deploy`
 * (an export step you call right after build_edge_bundle on legacy targets:
 *  python -m shims_chem.edge.deploy --export-c <bundle_dir>).
 *
 * Format (little-endian):
 *   uint32 magic       = 0x53484D45 ('SHME')
 *   uint32 n_layers
 *   for each layer:
 *     uint32 in_dim
 *     uint32 out_dim
 *     uint8  activation   (0=none, 1=relu, 2=tanh)
 *     int8   W[out_dim * in_dim]      // packed row-major
 *     float  scale[out_dim]
 *     float  bias[out_dim]
 *
 * Inference: input is a length-`in_dim` float vector on stdin (JSON array,
 * one line). Output is the argmax class on stdout, then probs.
 *
 * Compile (Linux/Windows, no deps):
 *     cc -O2 -o shims_edge_runner runner.c -lm
 *
 * Runs comfortably in <200 KB resident on the Pentium II profile.
 */
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <math.h>

#define MAGIC 0x53484D45u

typedef struct {
    uint32_t in_dim, out_dim;
    uint8_t  act;
    int8_t  *W;
    float   *scale;
    float   *bias;
} Layer;

static int read_u32(FILE *f, uint32_t *out) { return fread(out, 4, 1, f) == 1; }

static int load_model(const char *path, Layer **layers, uint32_t *n_layers) {
    FILE *f = fopen(path, "rb");
    if (!f) return 0;
    uint32_t magic;
    if (!read_u32(f, &magic) || magic != MAGIC) { fclose(f); return 0; }
    if (!read_u32(f, n_layers)) { fclose(f); return 0; }

    *layers = calloc(*n_layers, sizeof(Layer));
    for (uint32_t i = 0; i < *n_layers; i++) {
        Layer *L = &(*layers)[i];
        if (!read_u32(f, &L->in_dim) || !read_u32(f, &L->out_dim)) { fclose(f); return 0; }
        if (fread(&L->act, 1, 1, f) != 1) { fclose(f); return 0; }
        size_t Wsize = (size_t)L->in_dim * L->out_dim;
        L->W = malloc(Wsize);
        L->scale = malloc(L->out_dim * sizeof(float));
        L->bias = malloc(L->out_dim * sizeof(float));
        if (fread(L->W, 1, Wsize, f) != Wsize) { fclose(f); return 0; }
        if (fread(L->scale, sizeof(float), L->out_dim, f) != L->out_dim) { fclose(f); return 0; }
        if (fread(L->bias,  sizeof(float), L->out_dim, f) != L->out_dim) { fclose(f); return 0; }
    }
    fclose(f);
    return 1;
}

static void forward(Layer *layers, uint32_t n, float *x, uint32_t in_dim,
                    float *out, uint32_t *out_dim) {
    float *buf_a = malloc(in_dim * sizeof(float));
    memcpy(buf_a, x, in_dim * sizeof(float));
    uint32_t cur = in_dim;
    for (uint32_t li = 0; li < n; li++) {
        Layer *L = &layers[li];
        float *buf_b = calloc(L->out_dim, sizeof(float));
        /* y_i = scale_i * sum_j W_ij * x_j  + bias_i ;  W in {-1,0,1} so no float mul */
        for (uint32_t i = 0; i < L->out_dim; i++) {
            float acc = 0.0f;
            int8_t *row = &L->W[(size_t)i * L->in_dim];
            for (uint32_t j = 0; j < L->in_dim; j++) {
                int8_t w = row[j];
                if (w == 1)       acc += buf_a[j];
                else if (w == -1) acc -= buf_a[j];
            }
            float y = acc * L->scale[i] + L->bias[i];
            if (L->act == 1 && y < 0) y = 0;          /* relu */
            else if (L->act == 2)     y = tanhf(y);   /* tanh */
            buf_b[i] = y;
        }
        free(buf_a);
        buf_a = buf_b;
        cur = L->out_dim;
    }
    memcpy(out, buf_a, cur * sizeof(float));
    *out_dim = cur;
    free(buf_a);
}

static void softmax(float *x, uint32_t n) {
    float m = x[0];
    for (uint32_t i = 1; i < n; i++) if (x[i] > m) m = x[i];
    float s = 0;
    for (uint32_t i = 0; i < n; i++) { x[i] = expf(x[i] - m); s += x[i]; }
    for (uint32_t i = 0; i < n; i++) x[i] /= s;
}

int main(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "usage: %s model.bin\n", argv[0]); return 2; }
    Layer *layers; uint32_t n_layers;
    if (!load_model(argv[1], &layers, &n_layers)) {
        fprintf(stderr, "could not load model: %s\n", argv[1]); return 3;
    }

    /* Read one whitespace-separated line of floats from stdin */
    float xbuf[2048];
    uint32_t xn = 0;
    while (xn < 2048 && scanf("%f", &xbuf[xn]) == 1) xn++;

    float out[256]; uint32_t out_dim = 0;
    forward(layers, n_layers, xbuf, xn, out, &out_dim);
    softmax(out, out_dim);

    uint32_t best = 0;
    for (uint32_t i = 1; i < out_dim; i++) if (out[i] > out[best]) best = i;
    printf("%u", best);
    for (uint32_t i = 0; i < out_dim; i++) printf(" %.6f", out[i]);
    printf("\n");
    return 0;
}
