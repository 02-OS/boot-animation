#define _POSIX_C_SOURCE 200809L
#include <assert.h>
#include <errno.h>
#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#include <cairo.h>
#include "script.h"
#include "script-parse.h"
#include "script-execute.h"
#include "script-object.h"
#include "script-lib-image.h"
#include "script-lib-sprite.h"
#include "script-lib-plymouth.h"
#include "script-lib-math.h"
#include "script-lib-string.h"
#include "ply-list.h"

typedef struct {
    unsigned int width;
    unsigned int height;
    double origin_x;
    double origin_y;
    uint32_t backdrop_rgba;
} portal_spec_t;

static double number(script_state_t *s, const char *name) {
    return script_obj_hash_get_number(s->global, name);
}

static char *string(script_state_t *s, const char *name) {
    return script_obj_hash_get_string(s->global, name);
}

static sprite_t *sprite_at(script_lib_sprite_data_t *d, int z) {
    for (ply_list_node_t *n = ply_list_get_first_node(d->sprite_list); n; n = ply_list_get_next_node(d->sprite_list, n)) {
        sprite_t *sprite = ply_list_node_get_data(n);
        if (sprite->z == z) return sprite;
    }
    return NULL;
}

static void screenshot(script_lib_sprite_data_t *d, uint32_t backdrop_rgba, const char *dir, const char *filename, char *out_path, size_t out_size) {
    int written = snprintf(out_path, out_size, "%s/%s", dir, filename);
    assert(written > 0 && (size_t)written < out_size);

    ply_pixel_buffer_t *buffer = ply_pixel_buffer_new(d->max_width, d->max_height);
    ply_pixel_buffer_fill_with_hex_color(buffer, NULL, backdrop_rgba);
    for (int z = 0; z <= 10; z++) {
        for (ply_list_node_t *n = ply_list_get_first_node(d->sprite_list); n; n = ply_list_get_next_node(d->sprite_list, n)) {
            sprite_t *sprite = ply_list_node_get_data(n);
            if (sprite->z != z || !sprite->image || sprite->opacity <= 0) continue;
            ply_pixel_buffer_fill_with_buffer_at_opacity(buffer, sprite->image, sprite->x, sprite->y, sprite->opacity);
        }
    }
    cairo_surface_t *surface = cairo_image_surface_create_for_data(
        (unsigned char *)ply_pixel_buffer_get_argb32_data(buffer),
        CAIRO_FORMAT_ARGB32, d->max_width, d->max_height, d->max_width * 4);
    assert(cairo_surface_write_to_png(surface, out_path) == CAIRO_STATUS_SUCCESS);
    cairo_surface_destroy(surface);
    ply_pixel_buffer_free(buffer);
}

static bool parse_json_double(const char *json, const char *key, double *out) {
    char needle[64];
    snprintf(needle, sizeof(needle), "\"%s\"", key);
    const char *pos = strstr(json, needle);
    if (!pos) return false;
    pos = strchr(pos + strlen(needle), ':');
    if (!pos) return false;
    char *end = NULL;
    double val = strtod(pos + 1, &end);
    if (end == pos + 1) return false;
    *out = val;
    return true;
}

static bool parse_json_hex_rgba(const char *json, const char *key, uint32_t *out) {
    char needle[64];
    snprintf(needle, sizeof(needle), "\"%s\"", key);
    const char *pos = strstr(json, needle);
    if (!pos) return false;
    pos = strchr(pos + strlen(needle), '#');
    if (!pos) return false;
    unsigned int rgb = 0;
    if (sscanf(pos + 1, "%6x", &rgb) != 1) return false;
    *out = ((uint32_t)rgb << 8) | 0xffu;
    return true;
}

static void load_portal_spec(const char *theme_dir, const char *explicit_spec, portal_spec_t *spec) {
    spec->width = 1920;
    spec->height = 1080;
    spec->origin_x = -170.0;
    spec->origin_y = -4.0;
    spec->backdrop_rgba = 0xf9f9f7ff;

    char fallback_path[4096];
    const char *candidates[3] = { explicit_spec, "portal-spec.json", NULL };
    if (snprintf(fallback_path, sizeof(fallback_path), "%s/../../../../../portal-spec.json", theme_dir) < (int)sizeof(fallback_path)) {
        candidates[2] = fallback_path;
    }

    FILE *fp = NULL;
    for (int i = 0; i < 3; i++) {
        if (!candidates[i]) continue;
        fp = fopen(candidates[i], "rb");
        if (fp) break;
    }
    if (!fp) return;

    char buf[4096];
    size_t len = fread(buf, 1, sizeof(buf) - 1, fp);
    fclose(fp);
    buf[len] = '\0';

    double val = 0.0;
    if (parse_json_double(buf, "width", &val) && val > 0) spec->width = (unsigned int)val;
    if (parse_json_double(buf, "height", &val) && val > 0) spec->height = (unsigned int)val;
    if (parse_json_double(buf, "originX", &val)) spec->origin_x = val;
    if (parse_json_double(buf, "originY", &val)) spec->origin_y = val;
    parse_json_hex_rgba(buf, "backdrop", &spec->backdrop_rgba);
}

static uint8_t sprite_alpha_at(ply_pixel_buffer_t *buf, int x, int y) {
    unsigned long w = ply_pixel_buffer_get_width(buf);
    unsigned long h = ply_pixel_buffer_get_height(buf);
    assert(x >= 0 && y >= 0 && (unsigned long)x < w && (unsigned long)y < h);
    uint32_t pixel = ply_pixel_buffer_get_argb32_data(buf)[(unsigned long)y * w + (unsigned long)x];
    return (uint8_t)(pixel >> 24);
}

static void usage(const char *prog) {
    fprintf(stderr, "Usage: %s [--output-dir DIR] [--spec PATH] THEME_DIR SHUTDOWN(0|1)\n", prog);
}

int main(int argc, char **argv) {
    const char *dir = NULL;
    const char *shutdown_arg = NULL;
    const char *output_dir_arg = NULL;
    const char *spec_arg = NULL;

    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--output-dir") && i + 1 < argc) {
            output_dir_arg = argv[++i];
        } else if (!strncmp(argv[i], "--output-dir=", 13)) {
            output_dir_arg = argv[i] + 13;
        } else if (!strcmp(argv[i], "--spec") && i + 1 < argc) {
            spec_arg = argv[++i];
        } else if (!strncmp(argv[i], "--spec=", 7)) {
            spec_arg = argv[i] + 7;
        } else if (!strcmp(argv[i], "--help") || !strcmp(argv[i], "-h")) {
            usage(argv[0]);
            return 0;
        } else if (argv[i][0] == '-') {
            usage(argv[0]);
            return 2;
        } else if (!dir) {
            dir = argv[i];
        } else if (!shutdown_arg) {
            shutdown_arg = argv[i];
        } else {
            usage(argv[0]);
            return 2;
        }
    }

    if (!dir || !shutdown_arg) {
        usage(argv[0]);
        return 2;
    }

    int shutdown_triggered = atoi(shutdown_arg);
    portal_spec_t spec;
    load_portal_spec(dir, spec_arg, &spec);

    char owned_temp_dir[4096] = "";
    const char *out_dir = output_dir_arg;
    if (out_dir) {
        if (mkdir(out_dir, 0755) != 0 && errno != EEXIST) {
            perror("mkdir --output-dir");
            return 1;
        }
    } else {
        const char *tmpdir = getenv("TMPDIR");
        if (!tmpdir || !tmpdir[0]) tmpdir = "/tmp";
        size_t tlen = strlen(tmpdir);
        while (tlen > 1 && tmpdir[tlen - 1] == '/') tlen--;
        int n = snprintf(owned_temp_dir, sizeof(owned_temp_dir), "%.*s/02os-native-zero-portal-XXXXXX", (int)tlen, tmpdir);
        assert(n > 0 && (size_t)n < sizeof(owned_temp_dir));
        char *created = mkdtemp(owned_temp_dir);
        assert(created != NULL);
        out_dir = created;
    }

    char script_path[4096];
    int sn = snprintf(script_path, sizeof(script_path), "%s/02-zero-portal.script", dir);
    assert(sn > 0 && (size_t)sn < sizeof(script_path));

    script_op_t *op = script_parse_file(script_path);
    assert(op);
    script_state_t *s = script_state_new(NULL);
    ply_list_t *displays = ply_list_new();
    script_lib_image_data_t *image = script_lib_image_setup(s, (char *)dir);
    script_lib_sprite_data_t *sprites = script_lib_sprite_setup(s, displays);
    sprites->max_width = spec.width;
    sprites->max_height = spec.height;
    script_lib_plymouth_data_t *plymouth = script_lib_plymouth_setup(
        s,
        shutdown_triggered ? PLY_BOOT_SPLASH_MODE_SHUTDOWN : PLY_BOOT_SPLASH_MODE_BOOT_UP,
        30,
        NULL);
    script_lib_math_data_t *math = script_lib_math_setup(s);
    script_lib_string_data_t *str = script_lib_string_setup(s);

    char shot_paths[4][4096] = {{0}};
    int shot_count = 0;

    script_return_t result = script_execute(s, op);
    assert(result.type != SCRIPT_RETURN_TYPE_FAIL);
    script_obj_unref(result.object);

    sprite_t *logo = sprite_at(sprites, 2);
    assert(logo && logo->image);
    assert(sprite_at(sprites, 1) == NULL);
    assert(number(s, "last_frame") == -1);

    // Verify canonical portal-spec.json originX/originY alignment on the 1920x1080 logo sprite.
    double scale = number(s, "canvas_width") * 0.59375 / 680.0;
    double zero_cx = (double)spec.width / 2.0 + spec.origin_x * scale;
    double zero_cy = (double)spec.height / 2.0 + spec.origin_y * scale;
    assert(sprite_alpha_at(logo->image, (int)llround(zero_cx - logo->x), (int)llround(zero_cy - logo->y)) == 0);
    assert(sprite_alpha_at(logo->image, (int)llround(zero_cx + 132.0 * scale - logo->x), (int)llround(zero_cy - logo->y)) > 0);

    do {
        if (shutdown_triggered) {
            assert(number(s, "finished") == 1);
            assert(sprite_at(sprites, 2)->opacity == 1);
            script_lib_plymouth_on_boot_progress(s, plymouth, 42, 0.9);
            assert(number(s, "elapsed") == 0);
            puts("PASS shutdown never animates");
            break;
        }

        assert(sprite_at(sprites, 2)->opacity == 0);
        script_lib_plymouth_on_boot_progress(s, plymouth, 10, 0.3);
        script_lib_plymouth_on_boot_progress(s, plymouth, 10.125, 0.4);
        assert(fabs(number(s, "elapsed") - 0.125) < 1e-6);
        assert(fabs(sprite_at(sprites, 2)->opacity - 0.5) < 1e-6);
        for (int i = 0; i < 200; i++) script_lib_plymouth_on_refresh(s, plymouth);
        assert(fabs(number(s, "elapsed") - 0.125) < 1e-6);
        assert(fabs(sprite_at(sprites, 2)->opacity - 0.5) < 1e-6);
        script_lib_plymouth_on_boot_progress(s, plymouth, 9, 0.35);
        assert(fabs(number(s, "elapsed") - 0.125) < 1e-6);
        assert(fabs(sprite_at(sprites, 2)->opacity - 0.5) < 1e-6);
        screenshot(sprites, spec.backdrop_rgba, out_dir, "02os-native-zero-portal-fade.png",
                   shot_paths[shot_count], sizeof(shot_paths[0]));
        shot_count++;

        script_lib_plymouth_on_boot_progress(s, plymouth, 10.25, 0.5);
        assert(number(s, "finished") == 1);
        assert(sprite_at(sprites, 2)->opacity == 1);
        script_lib_plymouth_on_boot_progress(s, plymouth, 30, 0.6);
        assert(number(s, "finished") == 1);
        assert(sprite_at(sprites, 2)->opacity == 1);
        assert(fabs(number(s, "elapsed") - 0.25) < 1e-6);
        screenshot(sprites, spec.backdrop_rgba, out_dir, "02os-native-zero-portal-hold.png",
                   shot_paths[shot_count], sizeof(shot_paths[0]));
        shot_count++;

        script_lib_plymouth_on_display_message(s, plymouth, "Checking the filesystem");
        char *text = string(s, "message_text");
        assert(!strcmp(text, "Checking the filesystem"));
        free(text);
        script_lib_plymouth_on_hide_message(s, plymouth, "Checking the filesystem");
        text = string(s, "message_text");
        assert(!strcmp(text, ""));
        free(text);

        script_lib_plymouth_on_display_password(s, plymouth, "Unlock encrypted disk", 7);
        text = string(s, "entry_text");
        assert(!strcmp(text, "•••••••"));
        free(text);
        assert(number(s, "prompt_active") == 1);
        assert(sprite_at(sprites, 2)->opacity == 1);
        screenshot(sprites, spec.backdrop_rgba, out_dir, "02os-native-zero-portal-password.png",
                   shot_paths[shot_count], sizeof(shot_paths[0]));
        shot_count++;

        script_lib_plymouth_on_display_prompt(s, plymouth, "Secret", "password", true);
        text = string(s, "entry_text");
        assert(!strcmp(text, "••••••••"));
        free(text);
        script_lib_plymouth_on_display_question(s, plymouth, "Continue?", "yes");
        text = string(s, "entry_text");
        assert(!strcmp(text, "yes_"));
        free(text);
        script_lib_plymouth_on_display_normal(s, plymouth);
        assert(number(s, "prompt_active") == 0);
        for (ply_list_node_t *n = ply_list_get_first_node(sprites->sprite_list); n; n = ply_list_get_next_node(sprites->sprite_list, n)) {
            sprite_t *sprite = ply_list_node_get_data(n);
            if (sprite->z == 10) assert(sprite->opacity == 0);
        }

        sprites->max_width = 800;
        sprites->max_height = 600;
        script_lib_plymouth_on_display_hotplug(s, plymouth);
        assert(number(s, "canvas_width") == 320);
        assert(sprite_at(sprites, 2)->image);
        assert(ply_pixel_buffer_get_width(sprite_at(sprites, 2)->image) == 320);
        assert(sprite_at(sprites, 2)->opacity == 1);
        script_lib_plymouth_on_refresh(s, plymouth);
        assert(number(s, "canvas_width") == 320);
        assert(sprite_at(sprites, 2)->image);
        assert(sprite_at(sprites, 2)->opacity == 1);
        screenshot(sprites, spec.backdrop_rgba, out_dir, "02os-native-zero-portal-800x600.png",
                   shot_paths[shot_count], sizeof(shot_paths[0]));
        shot_count++;

        script_lib_plymouth_on_display_question(s, plymouth, "Continue?", "yes");
        script_lib_plymouth_on_quit(s, plymouth);
        assert(sprite_at(sprites, 2)->opacity == 1);
        assert(number(s, "prompt_active") == 0);
        for (ply_list_node_t *n = ply_list_get_first_node(sprites->sprite_list); n; n = ply_list_get_next_node(sprites->sprite_list, n)) {
            sprite_t *sprite = ply_list_node_get_data(n);
            if (sprite->z == 10) assert(sprite->opacity == 0);
        }
        puts("PASS native Zero Portal logo parse/assets, 0.25-second elapsed fade, repeated refreshes, nonmonotonic progress, static boot hold, password/question/message callbacks, resize, and quit");
    } while (0);

    script_state_destroy(s);
    script_lib_sprite_destroy(sprites);
    script_lib_image_destroy(image);
    script_lib_plymouth_destroy(plymouth);
    script_lib_math_destroy(math);
    script_lib_string_destroy(str);
    script_parse_op_free(op);
    ply_list_free(displays);

    if (owned_temp_dir[0]) {
        for (int i = 0; i < shot_count; i++) {
            unlink(shot_paths[i]);
        }
        rmdir(owned_temp_dir);
    }

    return 0;
}
