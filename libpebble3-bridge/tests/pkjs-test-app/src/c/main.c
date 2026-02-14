#include <pebble.h>

static Window *s_window;
static TextLayer *s_title_layer;
static TextLayer *s_result_layer;
static TextLayer *s_status_layer;
static char s_result_buf[128];
static char s_status_buf[64];

#define KEY_COMMAND     0
#define KEY_TEMPERATURE 1
#define KEY_CITY        2
#define KEY_STATUS      3
#define KEY_E2E_ACK     4

/* Pending E2E ack queue (simple ring buffer) */
#define ACK_QUEUE_SIZE 8
static char s_ack_queue[ACK_QUEUE_SIZE][128];
static int s_ack_head = 0;
static int s_ack_count = 0;
static bool s_ack_sending = false;

static void update_status(const char *text) {
    snprintf(s_status_buf, sizeof(s_status_buf), "%s", text);
    text_layer_set_text(s_status_layer, s_status_buf);
}

static void try_send_next_ack(void *ctx);

/* Try to send the next queued ack */
static void try_send_next_ack(void *ctx) {
    if (s_ack_count == 0) {
        s_ack_sending = false;
        return;
    }

    DictionaryIterator *iter;
    AppMessageResult res = app_message_outbox_begin(&iter);
    if (res == APP_MSG_OK) {
        dict_write_cstring(iter, KEY_E2E_ACK, s_ack_queue[s_ack_head]);
        dict_write_end(iter);
        app_message_outbox_send();
        APP_LOG(APP_LOG_LEVEL_INFO, "E2E_ACK_SENT: %s", s_ack_queue[s_ack_head]);
        s_ack_head = (s_ack_head + 1) % ACK_QUEUE_SIZE;
        s_ack_count--;
        s_ack_sending = true;
    } else {
        /* Outbox busy, retry in 200ms */
        APP_LOG(APP_LOG_LEVEL_INFO, "E2E_ACK_RETRY (outbox busy)");
        app_timer_register(200, try_send_next_ack, NULL);
        s_ack_sending = true;
    }
}

/* Queue an ack message to be sent back to JS */
static void queue_e2e_ack(const char *ack_msg) {
    if (s_ack_count >= ACK_QUEUE_SIZE) {
        APP_LOG(APP_LOG_LEVEL_ERROR, "E2E_ACK queue full, dropping: %s", ack_msg);
        return;
    }
    int tail = (s_ack_head + s_ack_count) % ACK_QUEUE_SIZE;
    snprintf(s_ack_queue[tail], sizeof(s_ack_queue[tail]), "%s", ack_msg);
    s_ack_count++;

    if (!s_ack_sending) {
        try_send_next_ack(NULL);
    }
}

static void inbox_received_handler(DictionaryIterator *iter, void *ctx) {
    Tuple *temp = dict_find(iter, KEY_TEMPERATURE);
    Tuple *city = dict_find(iter, KEY_CITY);
    Tuple *status = dict_find(iter, KEY_STATUS);

    /* Log every received key for E2E verification */
    APP_LOG(APP_LOG_LEVEL_INFO, "E2E_INBOX: temp=%s city=%s status=%s",
            temp ? "yes" : "no", city ? "yes" : "no",
            status ? "yes" : "no");

    if (temp && city && status) {
        int temperature = (int)temp->value->int32;
        const char *city_str = city->value->cstring;
        const char *status_str = status->value->cstring;

        snprintf(s_result_buf, sizeof(s_result_buf), "%s\n%d\xC2\xB0""C\n%s",
                 city_str, temperature, status_str);
        text_layer_set_text(s_result_layer, s_result_buf);
        update_status("Weather OK!");

        APP_LOG(APP_LOG_LEVEL_INFO, "E2E_WEATHER: T=%d C=%s S=%s",
                temperature, city_str, status_str);

        static char ack_buf[128];
        snprintf(ack_buf, sizeof(ack_buf), "WEATHER:T=%d,C=%s,S=%s",
                 temperature, city_str, status_str);
        queue_e2e_ack(ack_buf);

    } else if (status) {
        const char *status_str = status->value->cstring;
        snprintf(s_result_buf, sizeof(s_result_buf), "%s", status_str);
        text_layer_set_text(s_result_layer, s_result_buf);
        update_status("Got reply!");

        APP_LOG(APP_LOG_LEVEL_INFO, "E2E_STATUS: %s", status_str);

        static char ack_buf[128];
        snprintf(ack_buf, sizeof(ack_buf), "STATUS:%s", status_str);
        queue_e2e_ack(ack_buf);
    }
}

static void inbox_dropped_handler(AppMessageResult reason, void *ctx) {
    APP_LOG(APP_LOG_LEVEL_ERROR, "E2E_INBOX_DROPPED: reason=%d", (int)reason);
    update_status("Msg dropped");
}

static void outbox_sent_handler(DictionaryIterator *iter, void *ctx) {
    APP_LOG(APP_LOG_LEVEL_INFO, "E2E_OUTBOX_SENT");
    if (s_ack_count > 0) {
        app_timer_register(50, try_send_next_ack, NULL);
    } else {
        s_ack_sending = false;
    }
}

static void outbox_failed_handler(DictionaryIterator *iter,
                                   AppMessageResult reason, void *ctx) {
    APP_LOG(APP_LOG_LEVEL_ERROR, "E2E_OUTBOX_FAILED: reason=%d", (int)reason);
    update_status("Send failed");
    if (s_ack_count > 0) {
        app_timer_register(300, try_send_next_ack, NULL);
    } else {
        s_ack_sending = false;
    }
}

static void send_command(int cmd) {
    DictionaryIterator *iter;
    AppMessageResult res = app_message_outbox_begin(&iter);
    if (res == APP_MSG_OK) {
        dict_write_int32(iter, KEY_COMMAND, cmd);
        dict_write_end(iter);
        app_message_outbox_send();
        APP_LOG(APP_LOG_LEVEL_INFO, "E2E_CMD_SENT: %d", cmd);
        update_status("Sent cmd...");
    } else {
        APP_LOG(APP_LOG_LEVEL_ERROR, "E2E_CMD_FAILED: %d reason=%d", cmd, (int)res);
        update_status("Begin fail");
    }
}

static void select_click(ClickRecognizerRef ref, void *ctx) { send_command(1); }
static void up_click(ClickRecognizerRef ref, void *ctx) { send_command(2); }
static void down_click(ClickRecognizerRef ref, void *ctx) { send_command(3); }

static void click_config(void *ctx) {
    window_single_click_subscribe(BUTTON_ID_SELECT, select_click);
    window_single_click_subscribe(BUTTON_ID_UP, up_click);
    window_single_click_subscribe(BUTTON_ID_DOWN, down_click);
}

static void auto_cmd2(void *ctx) { send_command(2); }
static void auto_cmd3(void *ctx) { send_command(3); }

static void window_load(Window *window) {
    Layer *root = window_get_root_layer(window);
    GRect bounds = layer_get_bounds(root);

    s_title_layer = text_layer_create(GRect(0, 0, bounds.size.w, 28));
    text_layer_set_text(s_title_layer, "PKJS API Test");
    text_layer_set_text_alignment(s_title_layer, GTextAlignmentCenter);
    text_layer_set_font(s_title_layer,
        fonts_get_system_font(FONT_KEY_GOTHIC_18_BOLD));
    text_layer_set_background_color(s_title_layer, GColorBlack);
    text_layer_set_text_color(s_title_layer, GColorWhite);
    layer_add_child(root, text_layer_get_layer(s_title_layer));

    s_result_layer = text_layer_create(GRect(4, 32, bounds.size.w - 8, 90));
    text_layer_set_text(s_result_layer,
        "SEL: weather\nUP: config\nDOWN: timeline");
    text_layer_set_font(s_result_layer,
        fonts_get_system_font(FONT_KEY_GOTHIC_24_BOLD));
    text_layer_set_text_alignment(s_result_layer, GTextAlignmentCenter);
    text_layer_set_overflow_mode(s_result_layer, GTextOverflowModeWordWrap);
    layer_add_child(root, text_layer_get_layer(s_result_layer));

    s_status_layer = text_layer_create(GRect(0, 130, bounds.size.w, 38));
    text_layer_set_text(s_status_layer, "Ready");
    text_layer_set_text_alignment(s_status_layer, GTextAlignmentCenter);
    text_layer_set_font(s_status_layer,
        fonts_get_system_font(FONT_KEY_GOTHIC_18));
    layer_add_child(root, text_layer_get_layer(s_status_layer));
}

static void window_unload(Window *window) {
    text_layer_destroy(s_title_layer);
    text_layer_destroy(s_result_layer);
    text_layer_destroy(s_status_layer);
}

static void init(void) {
    app_message_register_inbox_received(inbox_received_handler);
    app_message_register_inbox_dropped(inbox_dropped_handler);
    app_message_register_outbox_sent(outbox_sent_handler);
    app_message_register_outbox_failed(outbox_failed_handler);
    app_message_open(512, 512);

    s_window = window_create();
    window_set_click_config_provider(s_window, click_config);
    window_set_window_handlers(s_window, (WindowHandlers) {
        .load = window_load,
        .unload = window_unload,
    });
    window_stack_push(s_window, true);

    APP_LOG(APP_LOG_LEVEL_INFO, "E2E_APP_STARTED");

    /* Auto-send commands: CMD 1 at 1s, CMD 2 at 6s, CMD 3 at 10s */
    app_timer_register(1000, (AppTimerCallback)select_click, NULL);
    app_timer_register(6000, auto_cmd2, NULL);
    app_timer_register(10000, auto_cmd3, NULL);
}

static void deinit(void) {
    window_destroy(s_window);
}

int main(void) {
    init();
    app_event_loop();
    deinit();
}
