// Intelligence8 Chat Widget
frappe.provide("brazil_module.i8");

brazil_module.i8.ChatWidget = class ChatWidget {
    constructor() {
        this.conversation = null;
        this.is_open = false;
        this.render();
    }

    render() {
        // Floating button
        this.$btn = $(`
            <div class="i8-chat-btn" title="Intelligence8 Chat">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
                </svg>
            </div>
        `).appendTo("body");

        // Chat panel
        this.$panel = $(`
            <div class="i8-chat-panel" style="display: none;">
                <div class="i8-chat-header">
                    <strong>Intelligence8</strong>
                    <button class="i8-chat-close btn btn-sm">&times;</button>
                </div>
                <div class="i8-chat-messages"></div>
                <div class="i8-chat-input">
                    <input type="text" placeholder="Pergunte ao agente..." class="form-control i8-chat-text">
                    <button class="btn btn-primary btn-sm i8-chat-send">Enviar</button>
                </div>
            </div>
        `).appendTo("body");

        this.bind_events();
    }

    bind_events() {
        this.$btn.on("click", () => this.toggle());
        this.$panel.find(".i8-chat-close").on("click", () => this.toggle());
        this.$panel.find(".i8-chat-send").on("click", () => this.send());
        this.$panel.find(".i8-chat-text").on("keypress", (e) => {
            if (e.which === 13) this.send();
        });
    }

    toggle() {
        this.is_open = !this.is_open;
        this.$panel.toggle(this.is_open);
        if (this.is_open && this.conversation) {
            this.load_history();
        }
    }

    send() {
        const $input = this.$panel.find(".i8-chat-text");
        const text = $input.val().trim();
        if (!text) return;

        this.add_message("human", text, "erp_chat");
        $input.val("");

        frappe.call({
            method: "brazil_module.api.i8_chat_send",
            args: { message: text, conversation: this.conversation },
            callback: (r) => {
                if (r.message) {
                    this.conversation = r.message.conversation;
                }
            },
        });
    }

    load_history() {
        if (!this.conversation) return;
        frappe.call({
            method: "brazil_module.api.i8_chat_history",
            args: { conversation: this.conversation },
            callback: (r) => {
                if (r.message && r.message.messages) {
                    this.$panel.find(".i8-chat-messages").empty();
                    r.message.messages.forEach((msg) => {
                        this.add_message(msg.actor, msg.content, msg.channel);
                    });
                }
            },
        });
    }

    add_message(actor, content, channel) {
        const cls = actor === "human" ? "i8-msg-human" : "i8-msg-agent";
        const badge = channel !== "erp_chat" ? `<span class="i8-msg-channel">${channel}</span>` : "";
        this.$panel.find(".i8-chat-messages").append(
            `<div class="i8-msg ${cls}">${badge}<span class="i8-msg-text">${frappe.utils.xss_sanitise(content)}</span></div>`
        );
        // Scroll to bottom
        const $msgs = this.$panel.find(".i8-chat-messages");
        $msgs.scrollTop($msgs[0].scrollHeight);
    }
};

// CSS
$("head").append(`
<style>
.i8-chat-btn {
    position: fixed;
    bottom: 24px;
    right: 24px;
    width: 48px;
    height: 48px;
    border-radius: 50%;
    background: var(--primary);
    color: white;
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    box-shadow: 0 2px 8px rgba(0,0,0,0.2);
    z-index: 1050;
}
.i8-chat-btn:hover { transform: scale(1.1); }
.i8-chat-panel {
    position: fixed;
    bottom: 80px;
    right: 24px;
    width: 380px;
    height: 500px;
    background: var(--card-bg);
    border: 1px solid var(--border-color);
    border-radius: 8px;
    box-shadow: 0 4px 16px rgba(0,0,0,0.15);
    display: flex;
    flex-direction: column;
    z-index: 1050;
}
.i8-chat-header {
    padding: 12px 16px;
    border-bottom: 1px solid var(--border-color);
    display: flex;
    justify-content: space-between;
    align-items: center;
}
.i8-chat-messages {
    flex: 1;
    overflow-y: auto;
    padding: 12px;
}
.i8-chat-input {
    padding: 8px 12px;
    border-top: 1px solid var(--border-color);
    display: flex;
    gap: 8px;
}
.i8-chat-input input { flex: 1; }
.i8-msg {
    margin-bottom: 8px;
    padding: 8px 12px;
    border-radius: 8px;
    max-width: 85%;
    word-wrap: break-word;
}
.i8-msg-human {
    background: var(--primary);
    color: white;
    margin-left: auto;
}
.i8-msg-agent {
    background: var(--subtle-fg);
}
.i8-msg-channel {
    font-size: 10px;
    opacity: 0.7;
    display: block;
    margin-bottom: 4px;
}
</style>
`);

// Auto-init when page loads (only for desk)
$(document).ready(() => {
    if (frappe.boot && frappe.boot.user && frappe.boot.user.name !== "Guest") {
        brazil_module.i8.chat = new brazil_module.i8.ChatWidget();
    }
});
