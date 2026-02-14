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

static void update_status(const char *text) {
    snprintf(s_status_buf, sizeof(s_status_buf), "%s", text);
    text_layer_set_text(s_status_layer, s_status_buf);
}

static void inbox_received_handler(DictionaryIterator *iter, void *ctx) {
    Tuple *temp = dict_find(iter, KEY_TEMPERATURE);
    Tuple *city = dict_find(iter, KEY_CITY);
    Tuple *status = dict_find(iter, KEY_STATUS);

    if (temp && city) {
        snprintf(s_result_buf, sizeof(s_result_buf), "%s\n%d°C",
                 city->value->cstring,
                 (int)temp->value->int32);
        text_layer_set_text(s_result_layer, s_result_buf);
    } else if (status) {
        snprintf(s_result_buf, sizeof(s_result_buf), "%s", status->value->cstring);
        text_layer_set_text(s_result_layer, s_result_buf);
    }

    update_status("Got reply!");
    APP_LOG(APP_LOG_LEVEL_INFO, "Inbox: temp=%s city=%s status=%s",
            temp ? "yes" : "no", city ? "yes" : "no", status ? "yes" : "no");
}

static void inbox_dropped_handler(AppMessageResult reason, void *ctx) {
    APP_LOG(APP_LOG_LEVEL_ERROR, "Inbox dropped: %d", (int)reason);
    update_status("Msg dropped");
}

static void outbox_sent_handler(DictionaryIterator *iter, void *ctx) {
    APP_LOG(APP_LOG_LEVEL_INFO, "Outbox sent OK");
}

static void outbox_failed_handler(DictionaryIterator *iter,
                                   AppMessageResult reason, void *ctx) {
    APP_LOG(APP_LOG_LEVEL_ERROR, "Outbox failed: %d", (int)reason);
    update_status("Send failed");
}

/* Send a command to JS */
static void send_command(int cmd) {
    DictionaryIterator *iter;
    AppMessageResult res = app_message_outbox_begin(&iter);
    if (res == APP_MSG_OK) {
        dict_write_int32(iter, KEY_COMMAND, cmd);
        dict_write_end(iter);
        app_message_outbox_send();
        update_status("Sent cmd...");
    } else {
        APP_LOG(APP_LOG_LEVEL_ERROR, "outbox_begin failed: %d", (int)res);
        update_status("Begin fail");
    }
}

static void select_click(ClickRecognizerRef ref, void *ctx) {
    send_command(1);  /* request weather */
}

static void up_click(ClickRecognizerRef ref, void *ctx) {
    send_command(2);  /* request config test */
}

static void down_click(ClickRecognizerRef ref, void *ctx) {
    send_command(3);  /* request timeline test */
}

static void click_config(void *ctx) {
    window_single_click_subscribe(BUTTON_ID_SELECT, select_click);
    window_single_click_subscribe(BUTTON_ID_UP, up_click);
    window_single_click_subscribe(BUTTON_ID_DOWN, down_click);
}

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

    /* Auto-send command 1 after 1 second */
    app_timer_register(1000, (AppTimerCallback)select_click, NULL);
}

static void deinit(void) {
    window_destroy(s_window);
}

int main(void) {
    init();
    app_event_loop();
    deinit();
}
