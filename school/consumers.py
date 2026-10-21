import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async

# Messages only the teacher (the person who created the live class) may send.
HOST_ONLY = {'screen_share', 'poll_create', 'poll_end', 'question_answered', 'mic_control', 'end_class'}

MAX_CHAT = 500
MAX_QUESTION = 300


class LiveVideoConsumer(AsyncWebsocketConsumer):
    """
    Signalling + chat for one live class room.

    * Only people allowed to see the class (same rule as the live_room view) may connect.
    * Teacher-only actions are checked here on the server, not just hidden in the page.
    * Every broadcast goes through `relay`; a message with a target_user_id (WebRTC offer / answer /
      ICE, mic control) is delivered only to that person.
    """

    # ───────────────────────── connection ─────────────────────────
    async def connect(self):
        self.room_name = self.scope['url_route']['kwargs']['room_name']
        self.room_group_name = f'live_{self.room_name}'
        self.user = self.scope['user']
        self.joined = False
        self.is_host = False
        self._voted = set()

        if not self.user.is_authenticated:
            await self.close()
            return

        status, reason = await self.check_access()
        await self.accept()

        if status != 'ok':
            # Tell the page why (so it does not keep reconnecting), then close.
            await self.send(text_data=json.dumps({'type': 'rejected', 'reason': reason}))
            await self.close(code=4403)
            return

        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        self.joined = True
        await self.add_participant()
        await self.broadcast({'type': 'user_joined', 'user': self.user.username, 'user_id': self.user.id})

    async def disconnect(self, close_code):
        if not self.joined:
            return
        self.joined = False
        await self.remove_participant()
        await self.channel_layer.group_discard(self.room_group_name, self.channel_name)
        await self.broadcast({'type': 'user_left', 'user': self.user.username, 'user_id': self.user.id})

    # ───────────────────────── incoming ─────────────────────────
    async def receive(self, text_data=None, bytes_data=None):
        if not text_data:
            return
        try:
            data = json.loads(text_data)
        except ValueError:
            return
        if not isinstance(data, dict):
            return

        kind = data.get('type')
        if kind in HOST_ONLY and not self.is_host:
            return

        msg = {'type': kind, 'user': self.user.username, 'user_id': self.user.id}

        if kind in ('offer', 'answer', 'ice_candidate'):
            key = 'candidate' if kind == 'ice_candidate' else kind
            if key not in data:
                return
            msg[key] = data[key]
            msg['target_user_id'] = data.get('target_user_id')

        elif kind == 'video_toggle':
            msg['enabled'] = bool(data.get('enabled', True))
            await self.update_participant(is_video_on=msg['enabled'])

        elif kind == 'audio_toggle':
            msg['enabled'] = bool(data.get('enabled', True))
            await self.update_participant(is_audio_on=msg['enabled'])

        elif kind == 'screen_share':
            msg['enabled'] = bool(data.get('enabled', False))

        elif kind == 'chat_message':
            text = str(data.get('message', '')).strip()[:MAX_CHAT]
            if not text:
                return
            msg['message'] = text

        elif kind == 'raise_hand':
            msg['raised'] = bool(data.get('raised', True))

        elif kind == 'question_ask':
            text = str(data.get('question', '')).strip()[:MAX_QUESTION]
            if not text:
                return
            msg['question'] = text
            msg['question_id'] = str(data.get('question_id', ''))[:64]

        elif kind == 'question_answered':
            msg['question_id'] = str(data.get('question_id', ''))[:64]

        elif kind == 'poll_create':
            options = [str(o).strip()[:100] for o in (data.get('options') or []) if str(o).strip()][:8]
            question = str(data.get('question', '')).strip()[:200]
            if not question or len(options) < 2:
                return
            msg.update({'poll_id': str(data.get('poll_id', ''))[:64], 'question': question, 'options': options})

        elif kind == 'poll_vote':
            poll_id = str(data.get('poll_id', ''))[:64]
            try:
                index = int(data.get('option_index', 0))
            except (TypeError, ValueError):
                return
            if poll_id in self._voted:          # one vote per person per poll
                return
            self._voted.add(poll_id)
            msg.update({'poll_id': poll_id, 'option_index': index})

        elif kind == 'poll_end':
            msg['poll_id'] = str(data.get('poll_id', ''))[:64]

        elif kind == 'mic_control':
            try:
                msg['target_user_id'] = int(data.get('target_user_id'))
            except (TypeError, ValueError):
                return
            msg['enabled'] = bool(data.get('enabled', True))

        elif kind == 'end_class':
            await self.close_session()
            msg['type'] = 'class_ended'

        else:
            return

        await self.broadcast(msg)

    # ───────────────────────── outgoing ─────────────────────────
    async def broadcast(self, payload):
        await self.channel_layer.group_send(self.room_group_name, {'type': 'relay', 'payload': payload})

    async def relay(self, event):
        payload = event['payload']
        target = payload.get('target_user_id')
        # Addressed to somebody else (WebRTC signalling, mic control): not for this connection.
        if target is not None and str(target) != str(self.user.id):
            return
        await self.send(text_data=json.dumps(payload))

    # ───────────────────────── database ─────────────────────────
    @database_sync_to_async
    def check_access(self):
        """('ok', '') when this user may be in this room, else (status, message for the page)."""
        from school.models import LiveParticipant
        from school.views import _live_sessions_for
        try:
            # Same rule as the live_room page: a student only gets their own class + registered subjects.
            session = _live_sessions_for(self.user).filter(room_name=self.room_name).first()
            if session is None:
                return 'denied', 'You are not allowed to join this class.'
            if not session.is_active:
                return 'ended', 'This class is not running.'
            self.is_host = session.created_by_id == self.user.id
            connected = (LiveParticipant.objects
                         .filter(session=session, is_connected=True)
                         .exclude(user=self.user).count())
            if connected >= session.max_participants:
                return 'full', 'This class is full.'
            return 'ok', ''
        except Exception as e:
            print(f'Error checking live room access: {e}')
            return 'denied', 'Could not join this class.'

    @database_sync_to_async
    def add_participant(self):
        from school.models import LiveSession, LiveParticipant
        try:
            session = LiveSession.objects.get(room_name=self.room_name)
            participant, created = LiveParticipant.objects.get_or_create(
                session=session, user=self.user,
                defaults={'is_connected': True, 'is_video_on': True, 'is_audio_on': self.is_host},
            )
            if not created:
                participant.is_connected = True
                participant.save(update_fields=['is_connected'])
        except Exception as e:
            print(f'Error adding participant: {e}')

    @database_sync_to_async
    def remove_participant(self):
        # The class stays open when the room empties (e.g. the teacher refreshes the page).
        # It is closed by the teacher's "End class" button.
        from school.models import LiveParticipant
        try:
            LiveParticipant.objects.filter(
                session__room_name=self.room_name, user=self.user
            ).update(is_connected=False)
        except Exception as e:
            print(f'Error removing participant: {e}')

    @database_sync_to_async
    def update_participant(self, **fields):
        from school.models import LiveParticipant
        try:
            LiveParticipant.objects.filter(
                session__room_name=self.room_name, user=self.user
            ).update(**fields)
        except Exception as e:
            print(f'Error updating participant: {e}')

    @database_sync_to_async
    def close_session(self):
        from school.models import LiveSession
        try:
            LiveSession.objects.filter(room_name=self.room_name).update(is_active=False)
        except Exception as e:
            print(f'Error closing session: {e}')
