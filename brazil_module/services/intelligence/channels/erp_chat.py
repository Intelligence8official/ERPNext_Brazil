import frappe
from brazil_module.services.intelligence.channels.channel_router import ChannelRouter


def send_message(user: str, text: str, conversation: str | None = None) -> dict:
    router = ChannelRouter()
    conv_name = router.route_message(
        channel="erp_chat",
        direction="incoming",
        actor="human",
        content=text,
        related_doctype="I8 Conversation" if conversation else None,
        related_docname=conversation,
    )
    frappe.enqueue(
        "brazil_module.services.intelligence.agent.process_single_event",
        queue="long",
        job_id=f"i8:erp_chat:{frappe.utils.now_datetime()}",
        event_type="human_message",
        event_id=f"erp_chat:{frappe.utils.now_datetime()}",
        event_data={
            "module": "conversation",
            "text": text,
            "user": user,
            "conversation_name": conv_name,
        },
        deduplicate=True,
    )
    return {"status": "sent", "conversation": conv_name}


def get_conversation_history(conversation_name: str) -> dict:
    conv = frappe.get_doc("I8 Conversation", conversation_name)
    messages = []
    for msg in (conv.messages or []):
        messages.append({
            "channel": msg.channel,
            "direction": msg.direction,
            "actor": msg.actor,
            "content": msg.content,
            "timestamp": str(msg.timestamp),
        })
    return {"conversation": conversation_name, "messages": messages}
