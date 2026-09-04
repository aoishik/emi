import re
import traceback

from pydantic import BaseModel
from typing import Any
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from slack_bolt.adapter.starlette.async_handler import AsyncSlackRequestHandler
from slack_bolt.app.async_app import AsyncApp

from emi.payloads import FulfillFullfilledPayload, FulfillOrderPayload
from emi.settings import Settings, settings

class SlackDispatchResult(BaseModel):
	ok: bool
	channel: str
	ts: str | None = None


class SlackRelay:
	# Channels exempt from channel manager check for /jusstudy-ping
	# TODO: move to config
	JUSSTUDY_PING_EXEMPT_CHANNELS = [
		"C02185NHSFK", #pingus-pongus
		"C0A6X2K6YTH", #jus-study-dev
	]

	def __init__(self, settings: Settings) -> None:
		self.settings = settings
		self.app = AsyncApp(
			token=settings.slack_bot_token,
			signing_secret=settings.slack_signing_secret,
		)
		self.handler = AsyncSlackRequestHandler(self.app)
		self._register_default_handlers()

	def _register_default_handlers(self) -> None:
		@self.app.action(re.compile(".*"))
		async def _ack_any_action(ack) -> None:
			await ack()

		@self.app.command("/jusstudy-ping")
		async def handle_jusstudy_ping(ack, command, client, say) -> None:
			await ack()
			await self._process_jusstudy_ping(command, client, say)

		@self.app.event("app_mention")
		async def handle_message_events(body, logger) -> None:
			"""Handle message events (logs for now; can be extended later)."""
			logger.info(f"Message event received: {body.get('event', {}).get('type')}")

	async def _is_channel_manager(self, channel_id: str, user_id: str) -> bool:
		"""Check if a user is a channel manager for the given channel."""
		try:
			info = await self.app.client.conversations_info(channel=channel_id)
			channel = info.get("channel", {})
			creator_user_id = channel.get("creator")
			if user_id == creator_user_id:
				return True
			
			# Check channel members with manager role or use conversations_members for moderators
			members = await self.app.client.conversations_members(channel=channel_id)
			member_list = members.get("members", [])
			
			# For now, check if user is the channel creator
			# In a production system, you might check Slack Connect or other RBAC mechanisms
			return user_id == creator_user_id
		except Exception:
			return False

	async def _process_jusstudy_ping(self, command: dict[str, Any], client: Any, say: Any) -> None:
		"""Process /jusstudy-ping command."""
		try:
			# Parse command text: [channel/here] [msg can be multiline]
			text = command.get("text", "").strip()
			if not text:
				await client.chat_postEphemeral(
					channel=command["channel_id"],
					user=command["user_id"],
					text="Usage: `/jusstudy-ping [channel/here] [message]`",
				)
				return

			# Split text to get channel and message
			parts = text.split(None, 1)  # Split on first whitespace
			channel_spec = parts[0]
			message = parts[1] if len(parts) > 1 else "(no message)"

			# Resolve channel
			target_channel = command["channel_id"]

			# Remove < > if channel is formatted as <#C123456> 
			if channel_spec.lower() == "here":
				message = f"<!here> {message}"
			else:
				message = f"<!channel> {message}"

			# Check if channel is exempt from CM check
			if target_channel not in self.JUSSTUDY_PING_EXEMPT_CHANNELS:
				# Check if user is channel manager
				is_cm = await self._is_channel_manager(target_channel, command["user_id"])
				if not is_cm:
					await client.chat_postEphemeral(
						channel=command["channel_id"],
						user=command["user_id"],
						text=":loll:You are not a channel manager. Only channel managers can use `/jusstudy-ping` in this channel.",
					)
					return

			blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": message}}]
			# Fetch user's profile for name and avatar
			user_info = await self.app.client.users_info(user=command["user_id"])
			user_profile = user_info.get("user", {})
			user_name = user_profile.get("profile", {}).get("display_name") or user_profile.get("real_name", "Unknown")
			user_avatar = user_profile.get("profile", {}).get("image_512") or user_profile.get("profile", {}).get("image_original", "")

			# Send message to target channel
			await client.chat_postMessage(
				channel=target_channel,
				text=message,
				blocks=blocks,
				username=user_name,
				icon_url=user_avatar,
			)
		except Exception as exc:
			await client.chat_postEphemeral(
				channel=command["channel_id"],
				user=command["user_id"],
				text=f"Error processing ping: {str(exc)[:100]}",
			)
			await self.log_error(
				"Error in /jusstudy-ping command",
				detail=f"Command: {command}\n\nError:\n{traceback.format_exc()}",
			)

	@staticmethod
	def _thread_detail_messages(detail: str | None, limit: int = 3500) -> list[str]:
		if not detail:
			return []
		safe_detail = detail.replace("```", "``\\`")
		chunk_size = max(1, limit - 8)
		chunks = [safe_detail[i : i + chunk_size] for i in range(0, len(safe_detail), chunk_size)]
		return [f"```\n{chunk}\n```" for chunk in chunks]

	async def log_error(self, message: str, detail: str | None = None) -> None:
		"""Best-effort error logging to the configured logging channel."""
		if not self.settings.logging_channel_id:
			return
		try:
			resp = await self.app.client.chat_postMessage(
				channel=self.settings.logging_channel_id,
				text=message,
			)
			thread_ts = resp.get("ts") if resp.get("ok") else None
			if thread_ts:
				messages = self._thread_detail_messages(detail)
				for thread_message in messages:
					await self.app.client.chat_postMessage(
						channel=self.settings.logging_channel_id,
						text=thread_message,
						thread_ts=thread_ts,
					)
				if self.settings.logging_cc_user_id:
					await self.app.client.chat_postMessage(
						channel=self.settings.logging_channel_id,
						text=f"CC: <@{self.settings.logging_cc_user_id}>",
						thread_ts=thread_ts,
					)
		except Exception as exc:
			print(f"Failed to write to logging channel: {exc}")

	async def _resolve_target_channel(self, target_id: str) -> str:
		if target_id.startswith("U"):
			conv = await self.app.client.conversations_open(users=target_id)
			return conv["channel"]["id"]
		if target_id.startswith("C") or target_id.startswith("G"):
			return target_id
		raise HTTPException(status_code=400, detail="target_id must start with U for DM or C/G for channel")

	@staticmethod
	def _format_order_fields(order_id: str, item_name: str, qty: str, cost: str) -> list[dict[str, str]]:
		return [
			{"type": "mrkdwn", "text": f"*Order ID:* {order_id}"},
			{"type": "mrkdwn", "text": f"*Item:* {item_name}"},
			{"type": "mrkdwn", "text": f"*Quantity:* {qty}"},
			{"type": "mrkdwn", "text": f"*Total:* {cost}"},
		]

	async def _send_order_update_dm(
		self,
		user_id: str,
		headline: str,
		status_line: str,
		order_id: str,
		item_name: str,
		qty: str,
		cost: str,
		closing_line: str,
		extra_lines: list[str] | None = None,
		extra_fields: list[dict[str, str]] | None = None,
	) -> SlackDispatchResult:
		channel = await self._resolve_target_channel(user_id)
		fields = self._format_order_fields(order_id, item_name, qty, cost) + (extra_fields or [])
		blocks = [
			{
				"type": "header",
				"text": {"type": "plain_text", "text": headline},
			},
			{
				"type": "section",
				"text": {"type": "mrkdwn", "text": f"*Your Order Status:* {status_line}"},
			},
			{
				"type": "section",
				"text": {"type": "mrkdwn", "text": "*Order Details:*"},
				"fields": fields,
			},
			{
				"type": "divider",
			},
			{
				"type": "context",
				"elements": [
					{"type": "mrkdwn", "text": closing_line},
					*([{"type": "mrkdwn", "text": line} for line in (extra_lines or [])]),
				],
			},
		]

		resp = await self.app.client.chat_postMessage(
			channel=channel,
			text=f"{headline} {order_id}",
			blocks=blocks,
		)

		return SlackDispatchResult(ok=bool(resp["ok"]), channel=channel, ts=resp.get("ts"))

	async def send_custom_message(self, target_id: str, message: str) -> SlackDispatchResult:
		channel = await self._resolve_target_channel(target_id)
		resp = await self.app.client.chat_postMessage(
			channel=channel,
			text=message,
			blocks=[
				{
					"type": "section",
					"text": {"type": "mrkdwn", "text": message},
				},
			],
		)
		return SlackDispatchResult(ok=bool(resp["ok"]), channel=channel, ts=resp.get("ts"))

	async def send_block_kit(self, target_id: str, title: str, blocks: list[dict]) -> SlackDispatchResult:
		channel = await self._resolve_target_channel(target_id)
		resp = await self.app.client.chat_postMessage(
			channel=channel,
			text=title,
			blocks=blocks
		)
		return SlackDispatchResult(ok=bool(resp["ok"]), channel=channel, ts=resp.get("ts"))

	async def send_dm(self, user_id: str, project_name: str, project_link: str) -> SlackDispatchResult:
		# Open a DM channel with the user (conversations_open returns channel info)
		conv = await self.app.client.conversations_open(users=user_id)
		channel = conv["channel"]["id"]

		blocks = [
			{
				"type": "header",
				"text": {"type": "plain_text", "text": "Project Submitted for Review"},
			},
			{
				"type": "section",
				"text": {
					"type": "mrkdwn",
					"text": f"Your project <{project_link}|*{project_name}*> has been submitted for review.",
				},
			},
		]

		resp = await self.app.client.chat_postMessage(
			channel=channel,
			text=f"Your project {project_name} has been submitted for review.",
			blocks=blocks,
		)

		return SlackDispatchResult(ok=bool(resp["ok"]), channel=channel, ts=resp.get("ts"))


	async def post_review_accept(self, user_id: str, project_name: str, project_link: str, reviewer_id: str, feedback: str) -> dict[str, Any]:
		"""Post acceptance review with custom reviewer profile in channel and detailed message in DM."""
		ship_channel = self.settings.ship_channel_id
		if not ship_channel:
			raise HTTPException(status_code=400, detail="SHIP_CHANNEL_ID not configured")

		# Fetch reviewer's profile for name and avatar
		user_info = await self.app.client.users_info(user=reviewer_id)
		user_profile = user_info.get("user", {})
		reviewer_name = user_profile.get("profile", {}).get("display_name") or user_profile.get("real_name", "Unknown")
		reviewer_avatar = user_profile.get("profile", {}).get("image_512") or user_profile.get("profile", {}).get("image_original", "")

		# Post to ship channel spoofed as reviewer
		# channel_message = f"<@{user_id}> Your *{project_name}* has been reviewed. Please check your DM by <@U0B18V07GQ3> for details."
		blocks = [
		{
		"type": "container",
		"block_id": "bkb_container_icon_subtitle",
		"title": {
		"type": "plain_text",
		"text": f"{project_name}"
		},
		"child_blocks": [
		{
		"type": "context",
		"block_id": "context-r",
		"elements": [
		{
		"type": "mrkdwn",
		"text": f"Submitted By: <@{user_id}>"
		}
		]
		},
		{
		"type": "table",
		"block_id": "section-1",
		"rows": [
		[
		{
		"type": "rich_text",
		"elements": [
		{
		"type": "rich_text_section",
		"elements": [
		{
		"type": "text",
		"text": "Status",
		"style": {
		"bold": True
		}
		}
		]
		}
		]
		},
		{
		"type": "raw_text",
		"text": "🟢 Approved"
		}
		],
		[
		{
		"type": "rich_text",
		"elements": [
		{
		"type": "rich_text_section",
		"elements": [
		{
		"type": "text",
		"text": "feedback",
		"style": {
		"bold": True
		}
		}
		]
		}
		]
		},
		{
		"type": "raw_text",
		"text": f"{feedback}"
		}
		]
		]
		},
		{
		"type": "divider",
		"block_id": "divider-1"
		},
		{
		"type": "context",
		"block_id": "context-1",
		"elements": [
		{
		"type": "mrkdwn",
		"text": f"Reviewed By: <@{reviewer_id}>"
		}
		]
		}
		]
		},
		{
		"type": "context",
		"elements": [
		{
		"type": "mrkdwn",
		"text": f"<{project_link}|View Project> · {datetime.now(ZoneInfo("Asia/Kolkata")).strftime('%d-%m-%Y %H:%M:%S %Z')}"
		}
		]
		}
		]

		resp = await self.app.client.chat_postMessage(
			channel=ship_channel,
			blocks=blocks,
			username=reviewer_name,
			icon_url=reviewer_avatar
		)

		# Send detailed review to DM
		await self.send_review_dm_accept(user_id, project_name, project_link, reviewer_name, reviewer_id, feedback)

		return {"ok": bool(resp["ok"]), "channel": ship_channel, "ts": resp.get("ts")}

	async def post_review_reject(self, user_id: str, project_name: str, project_link: str, reviewer_id: str, feedback: str) -> dict[str, Any]:
		"""Post rejection review with custom reviewer profile in channel and detailed message in DM."""
		ship_channel = self.settings.ship_channel_id
		if not ship_channel:
			raise HTTPException(status_code=400, detail="SHIP_CHANNEL_ID not configured")

		# Fetch reviewer's profile for name and avatar
		user_info = await self.app.client.users_info(user=reviewer_id)
		user_profile = user_info.get("user", {})
		reviewer_name = user_profile.get("profile", {}).get("display_name") or user_profile.get("real_name", "Unknown")
		reviewer_avatar = user_profile.get("profile", {}).get("image_original") or user_profile.get("profile", {}).get("image_512", "")

		# Post to ship channel spoofed as reviewer
		blocks = [
		{
		"type": "container",
		"block_id": "bkb_container_icon_subtitle",
		"title": {
		"type": "plain_text",
		"text": f"{project_name}"
		},
		"child_blocks": [
		{
		"type": "context",
		"block_id": "context-r",
		"elements": [
		{
		"type": "mrkdwn",
		"text": f"Submitted By: <@{user_id}>"
		}
		]
		},
		{
		"type": "table",
		"block_id": "section-1",
		"rows": [
		[
		{
		"type": "rich_text",
		"elements": [
		{
		"type": "rich_text_section",
		"elements": [
		{
		"type": "text",
		"text": "Status",
		"style": {
		"bold": True
		}
		}
		]
		}
		]
		},
		{
		"type": "raw_text",
		"text": "🔴 Rejected"
		}
		],
		[
		{
		"type": "rich_text",
		"elements": [
		{
		"type": "rich_text_section",
		"elements": [
		{
		"type": "text",
		"text": "Reviewer Notes",
		"style": {
		"bold": True
		}
		}
		]
		}
		]
		},
		{
		"type": "raw_text",
		"text": f"{feedback}"
		}
		]
		]
		},
		{
		"type": "divider",
		"block_id": "divider-1"
		},
		{
		"type": "context",
		"block_id": "context-1",
		"elements": [
		{
		"type": "mrkdwn",
		"text": f"Reviewed By: <@{reviewer_id}>"
		}
		]
		}
		]
		},
		{
		"type": "context",
		"elements": [
		{
		"type": "mrkdwn",
		"text": f"<{project_link}|View Project> · {datetime.now(ZoneInfo("Asia/Kolkata")).strftime('%d-%m-%Y %H:%M:%S %Z')}"
		}
		]
		}
		]

		resp = await self.app.client.chat_postMessage(
			channel=ship_channel,
			blocks=blocks,
			username=reviewer_name,
			icon_url=reviewer_avatar
		)

		# Send detailed review to DM
		await self.send_review_dm_reject(user_id, project_name, project_link, reviewer_name, reviewer_id, feedback)

		return {"ok": bool(resp["ok"]), "channel": ship_channel, "ts": resp.get("ts")}

	async def send_review_dm_accept(self, user_id: str, project_name: str, project_link: str, reviewer_name: str, reviewer_id: str, feedback: str) -> SlackDispatchResult:
		"""Send detailed acceptance review to DM as mrkdwn blocks so it can be edited later."""
		conv = await self.app.client.conversations_open(users=user_id)
		channel = conv["channel"]["id"]

		blocks = [
			{
				"type": "header",
				"text": {"type": "plain_text", "text": ":tada: Project Reviewed. Congratulations!"},
			},
			{
				"type": "section",
				"text": {
					"type": "mrkdwn",
					"text": f"A reviewer has approved by your project *{project_name}*.",
				},
			},
			{
				"type": "section",
				"text": {
					"type": "mrkdwn",
					"text": f"*Acceptance Feedback:* {feedback}\n\n*Your project is now under Voting Stage.",
				},
			},
			{
				"type": "divider",
			},
			{
				"type": "context",
				"elements": [
					{"type": "mrkdwn", "text": "Keep up the great work and continue to study hard!"},
				],
			},
			{
				"type": "actions",
				"elements": [
					{
						"type": "button",
						"text": {"type": "plain_text", "text": "View Project"},
						"url": project_link,
					}
				],
			},
		]

		resp = await self.app.client.chat_postMessage(
			channel=channel,
			text=f"A reviewer reviewed {project_name}",
			blocks=blocks,
		)

		return SlackDispatchResult(ok=bool(resp["ok"]), channel=channel, ts=resp.get("ts"))

	async def send_fulfill_pending_dm(self, payload: FulfillOrderPayload) -> SlackDispatchResult:
		return await self._send_order_update_dm(
			user_id=payload.user_id,
			headline=f":shopping_trolley: Order #{payload.order_id} Update",
			status_line="Pending",
			order_id=payload.order_id,
			item_name=payload.item_name,
			qty=payload.qty,
			cost=payload.cost,
			closing_line="Thanking you for participating in Jus' Study with us!",
			extra_lines=None,
		)

	async def send_fulfill_approved_dm(self, payload: FulfillOrderPayload) -> SlackDispatchResult:
		return await self._send_order_update_dm(
			user_id=payload.user_id,
			headline=f":white_check_mark: Order #{payload.order_id} Approved!",
			status_line="Approved. Pending Fulfillment.",
			order_id=payload.order_id,
			item_name=payload.item_name,
			qty=payload.qty,
			cost=payload.cost,
			closing_line="We'll notify you when your order ships. Thank You for your patience!",
			extra_lines=None,
		)

	async def send_fulfill_reject_dm(self, payload: FulfillOrderPayload) -> SlackDispatchResult:
		comment_value = payload.comment or "(none)"
		return await self._send_order_update_dm(
			user_id=payload.user_id,
			headline=f":x: Order #{payload.order_id} Rejected",
			status_line="Rejected. Please review the notes from the team.",
			order_id=payload.order_id,
			item_name=payload.item_name,
			qty=payload.qty,
			cost=payload.cost,
			closing_line="If you have questions, reach out in the help channel.",
			extra_lines=None,
			extra_fields=[{"type": "mrkdwn", "text": f"*Comment:* {comment_value}"}],
		)

	async def send_fulfill_fullfilled_dm(self, payload: FulfillFullfilledPayload) -> SlackDispatchResult:
		channel = await self._resolve_target_channel(payload.user_id)
		blocks = [
			{
				"type": "header",
				"text": {"type": "plain_text", "text": f":tada: Order #{payload.order_id} Fulfilled!"},
			},
			{
				"type": "section",
				"text": {"type": "mrkdwn", "text": "*Your Order Status:* Your order has been fulfilled and is on its way! Make sure to show off what you do with it in <#C07UMRYJ1LH> when it arrives!"},
			},
			{
				"type": "section",
				"text": {"type": "mrkdwn", "text": "*Order Details:*"},
				"fields": self._format_order_fields(payload.order_id, payload.item_name, payload.qty, payload.cost)
				+ [
					{"type": "mrkdwn", "text": f"*Fulfilled By:* {payload.fulfilled_by}"},
					{"type": "mrkdwn", "text": f"*Tracking Details:* :package: {payload.tracking_details}"},
				],
			},
			{
				"type": "divider",
			},
			{
				"type": "context",
				"elements": [
					{"type": "mrkdwn", "text": "Thanking you for participating in Jus' Study with us!"},
				],
			},
		]

		resp = await self.app.client.chat_postMessage(
			channel=channel,
			text=f"Order #{payload.order_id} Fulfilled!",
			blocks=blocks,
		)

		return SlackDispatchResult(ok=bool(resp["ok"]), channel=channel, ts=resp.get("ts"))

	async def send_review_dm_reject(self, user_id: str, project_name: str, project_link: str, reviewer_name: str, reviewer_id: str, feedback: str) -> SlackDispatchResult:
		"""Send detailed rejection review to DM as mrkdwn blocks so it can be edited later."""
		conv = await self.app.client.conversations_open(users=user_id)
		channel = conv["channel"]["id"]

		blocks = [
			{
				"type": "header",
				"text": {"type": "plain_text", "text": ":x: Oof! Your project needs some changes..."},
			},
			{
				"type": "section",
				"text": {
					"type": "mrkdwn",
					"text": f"A reviewer has reviewed your project *{project_name}*."
				},
			},
			{
				"type": "section",
				"text": {
					"type": "mrkdwn",
					"text": f"*Rejection Feedback:* {feedback}"
				},
			},
			{
				"type": "divider",
			},
			{
				"type": "context",
				"elements": [
					{"type": "mrkdwn", "text": "Don't give up! Review the feedback, make improvements, and ship again! :muscle:"},
				],
			},
			{
				"type": "actions",
				"elements": [
					{
						"type": "button",
						"text": {"type": "plain_text", "text": "View Project"},
						"url": project_link,
					},
				],
			},
		]

		resp = await self.app.client.chat_postMessage(
			channel=channel,
			text=f"A reviewer has reviewed {project_name}",
			blocks=blocks,
		)

		return SlackDispatchResult(ok=bool(resp["ok"]), channel=channel, ts=resp.get("ts"))

	async def post_voting_complete(self, user_id: str, project_name: str, project_link: str,vote: str, feedback: str | None, currencies: str) -> dict[str, Any]:
		"""Post voting complete message with custom reviewer profile in channel and detailed message in DM."""
		ship_channel = self.settings.ship_channel_id
		if not ship_channel:
			raise HTTPException(status_code=400, detail="SHIP_CHANNEL_ID not configured")

		# Fetch reviewer's profile for name and avatar
		user_info = await self.app.client.users_info(user=user_id)
		user_profile = user_info.get("user", {})
		reviewer_name = user_profile.get("profile", {}).get("display_name") or user_profile.get("real_name", "Unknown")
		reviewer_avatar = user_profile.get("profile", {}).get("image_512") or user_profile.get("profile", {}).get("image_original", "")

		# Post to ship channel
		blocks = [
			{
				"type": "container",
				"block_id": "bkb_container_voting_complete",
				"title": {
					"type": "plain_text",
					"text": f"{project_name}"
				},
				"child_blocks": [
					{
						"type": "context",
						"block_id": "context-submitter",
						"elements": [
							{
								"type": "mrkdwn",
								"text": f"Submitted By: <@{user_id}>"
							}
						]
					},
					{
						"type": "table",
						"block_id": "section-voting",
						"rows": [
							[
								{
									"type": "rich_text",
									"elements": [
										{
											"type": "rich_text_section",
											"elements": [
												{
													"type": "text",
													"text": "Status",
													"style": {
														"bold": True
													}
												}
											]
										}
									]
								},
								{
									"type": "raw_text",
									"text": "🎉 Voting Complete"
								}
							],
							[
								{
									"type": "rich_text",
									"elements": [
										{
											"type": "rich_text_section",
											"elements": [
												{
													"type": "text",
													"text": "Reward",
													"style": {
														"bold": True
													}
												}
											]
										}
									]
								},
								{
									"type": "raw_text",
									"text": f"{currencies}"
								}
							],
							[
								{
									"type": "rich_text",
									"elements": [
										{
											"type": "rich_text_section",
											"elements": [
												{
													"type": "text",
													"text": "Voting Info",
													"style": {
														"bold": True
													}
												}
											]
										}
									]
								},
								{
									"type": "raw_text",
									"text": f"{vote}"
								}
							]
						]
					},
					{
						"type": "divider",
						"block_id": "divider-voting"
					},
					{
						"type": "context",
						"block_id": "context-reviewer",
						"elements": [
							{
								"type": "mrkdwn",
								"text": f"Voting Completed for {project_name} shipped by <@{user_id}>"
							}
						]
					}
				]
			},
			{
				"type": "context",
				"elements": [
					{
						"type": "mrkdwn",
						"text": f"<{project_link}|View Project> · {datetime.now(ZoneInfo("Asia/Kolkata")).strftime('%d-%m-%Y %H:%M:%S %Z')}"
					}
				]
			}
		]

		resp = await self.app.client.chat_postMessage(
			channel=ship_channel,
			blocks=blocks,
			username=reviewer_name,
			icon_url=reviewer_avatar
		)

		# Send detailed voting complete notification to DM
		await self.send_voting_complete_dm(user_id, project_name, project_link, vote, feedback, currencies)

		return {"ok": bool(resp["ok"]), "channel": ship_channel, "ts": resp.get("ts")}

	async def send_voting_complete_dm(self, user_id: str, project_name: str, project_link: str, vote: str, feedback: str | None, currencies: str) -> SlackDispatchResult:
		"""Send detailed voting complete notification to DM."""
		conv = await self.app.client.conversations_open(users=user_id)
		channel = conv["channel"]["id"]

		feedback_text = feedback if feedback else "Thank you for your participation!"
		blocks = [
			{
				"type": "header",
				"text": {"type": "plain_text", "text": ":trophy: Voting Complete! Congratulations!"},
			},
			{
				"type": "section",
				"text": {
					"type": "mrkdwn",
					"text": f"Your project *{project_name}* has completed the voting stage!",
				},
			},
			{
				"type": "section",
				"text": {
					"type": "mrkdwn",
					"text": f"*Reward Earned:* {currencies}",
				},
			},
			{
				"type": "section",
				"text": {
					"type": "mrkdwn",
					"text": f"*Voting Info:* {vote}",
				},
			},
			{
				"type": "section",
				"text": {
					"type": "mrkdwn",
					"text": f"*{'Feedback:' if feedback_text else 'Go Study now! Stop looking at Slack'}* {feedback_text if feedback_text else 'No feedback provided.'}",
				},
			},
			{
				"type": "divider",
			},
			{
				"type": "context",
				"elements": [
					{"type": "mrkdwn", "text": "Great work on completing the study! Keep shipping amazing projects! :star:"},
				],
			},
			{
				"type": "actions",
				"elements": [
					{
						"type": "button",
						"text": {"type": "plain_text", "text": "View Project"},
						"url": project_link,
					}
				],
			},
		]

		resp = await self.app.client.chat_postMessage(
			channel=channel,
			text=f"Voting complete for {project_name}",
			blocks=blocks,
		)

		return SlackDispatchResult(ok=bool(resp["ok"]), channel=channel, ts=resp.get("ts"))


slack_relay = SlackRelay(settings)