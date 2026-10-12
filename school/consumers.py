import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async


class LiveVideoConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.room_name = self.scope['url_route']['kwargs']['room_name']
        self.room_group_name = f'live_{self.room_name}'
        self.user = self.scope['user']

        # Check if user is authenticated
        if not self.user.is_authenticated:
            await self.close()
            return

        # Join room group
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )

        await self.accept()

        # Add user to participants
        await self.add_participant()

        # Notify others about new participant
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'user_joined',
                'user': self.user.username,
                'user_id': self.user.id
            }
        )

    async def disconnect(self, close_code):
        # Remove user from participants
        await self.remove_participant()

        # Leave room group
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )

        # Notify others about user leaving
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'user_left',
                'user': self.user.username,
                'user_id': self.user.id
            }
        )

    # Receive message from WebSocket
    async def receive(self, text_data):
        text_data_json = json.loads(text_data)
        message_type = text_data_json.get('type')

        if message_type == 'offer':
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'offer',
                    'offer': text_data_json['offer'],
                    'user': self.user.username,
                    'user_id': self.user.id,
                    'target_user_id': text_data_json.get('target_user_id')
                }
            )
        elif message_type == 'answer':
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'answer',
                    'answer': text_data_json['answer'],
                    'user': self.user.username,
                    'user_id': self.user.id,
                    'target_user_id': text_data_json.get('target_user_id')
                }
            )
        elif message_type == 'ice_candidate':
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'ice_candidate',
                    'candidate': text_data_json['candidate'],
                    'user': self.user.username,
                    'user_id': self.user.id,
                    'target_user_id': text_data_json.get('target_user_id')
                }
            )
        elif message_type == 'video_toggle':
            # Update participant's video status
            await self.update_video_status(text_data_json.get('enabled', True))
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'video_toggle',
                    'user': self.user.username,
                    'user_id': self.user.id,
                    'enabled': text_data_json.get('enabled', True)
                }
            )
        elif message_type == 'audio_toggle':
            # Update participant's audio status
            await self.update_audio_status(text_data_json.get('enabled', True))
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'audio_toggle',
                    'user': self.user.username,
                    'user_id': self.user.id,
                    'enabled': text_data_json.get('enabled', True)
                }
            )
        elif message_type == 'screen_share':
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'screen_share',
                    'user': self.user.username,
                    'user_id': self.user.id,
                    'enabled': text_data_json.get('enabled', False)
                }
            )
        elif message_type == 'chat_message':
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'chat_message',
                    'user': self.user.username,
                    'user_id': self.user.id,
                    'message': text_data_json.get('message', '')
                }
            )

        elif message_type == 'raise_hand':
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'raise_hand',
                    'user': self.user.username,
                    'user_id': self.user.id,
                    'raised': text_data_json.get('raised', True)
                }
            )

        elif message_type == 'reaction':
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'reaction',
                    'user': self.user.username,
                    'user_id': self.user.id,
                    'emoji': text_data_json.get('emoji', '👍')
                }
            )

        elif message_type == 'question_ask':
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'question_ask',
                    'user': self.user.username,
                    'user_id': self.user.id,
                    'question': text_data_json.get('question', ''),
                    'question_id': text_data_json.get('question_id', '')
                }
            )

        elif message_type == 'question_answered':
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'question_answered',
                    'user': self.user.username,
                    'user_id': self.user.id,
                    'question_id': text_data_json.get('question_id', '')
                }
            )

        elif message_type == 'poll_create':
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'poll_create',
                    'user': self.user.username,
                    'user_id': self.user.id,
                    'poll_id': text_data_json.get('poll_id', ''),
                    'question': text_data_json.get('question', ''),
                    'options': text_data_json.get('options', [])
                }
            )

        elif message_type == 'poll_vote':
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'poll_vote',
                    'user': self.user.username,
                    'user_id': self.user.id,
                    'poll_id': text_data_json.get('poll_id', ''),
                    'option_index': text_data_json.get('option_index', 0)
                }
            )

        elif message_type == 'poll_end':
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'poll_end',
                    'user': self.user.username,
                    'user_id': self.user.id,
                    'poll_id': text_data_json.get('poll_id', '')
                }
            )

        elif message_type == 'mic_control':
            # Broadcast teacher's mute/unmute command to all room members
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'mic_control',
                    'user': self.user.username,
                    'user_id': self.user.id,
                    'target_user_id': text_data_json.get('target_user_id'),
                    'enabled': text_data_json.get('enabled', True)
                }
            )

    async def offer(self, event):
        await self.send(text_data=json.dumps({
            'type': 'offer',
            'offer': event['offer'],
            'user': event['user'],
            'user_id': event['user_id'],
            'target_user_id': event.get('target_user_id')
        }))

    async def answer(self, event):
        await self.send(text_data=json.dumps({
            'type': 'answer',
            'answer': event['answer'],
            'user': event['user'],
            'user_id': event['user_id'],
            'target_user_id': event.get('target_user_id')
        }))

    async def ice_candidate(self, event):
        await self.send(text_data=json.dumps({
            'type': 'ice_candidate',
            'candidate': event['candidate'],
            'user': event['user'],
            'user_id': event['user_id'],
            'target_user_id': event.get('target_user_id')
        }))

    async def user_joined(self, event):
        await self.send(text_data=json.dumps({
            'type': 'user_joined',
            'user': event['user'],
            'user_id': event['user_id']
        }))

    async def user_left(self, event):
        await self.send(text_data=json.dumps({
            'type': 'user_left',
            'user': event['user'],
            'user_id': event['user_id']
        }))

    async def video_toggle(self, event):
        await self.send(text_data=json.dumps({
            'type': 'video_toggle',
            'user': event['user'],
            'user_id': event['user_id'],
            'enabled': event['enabled']
        }))

    async def audio_toggle(self, event):
        await self.send(text_data=json.dumps({
            'type': 'audio_toggle',
            'user': event['user'],
            'user_id': event['user_id'],
            'enabled': event['enabled']
        }))

    async def screen_share(self, event):
        await self.send(text_data=json.dumps({
            'type': 'screen_share',
            'user': event['user'],
            'user_id': event['user_id'],
            'enabled': event['enabled']
        }))

    async def chat_message(self, event):
        await self.send(text_data=json.dumps({
            'type': 'chat_message',
            'user': event['user'],
            'user_id': event['user_id'],
            'message': event['message']
        }))

    async def raise_hand(self, event):
        await self.send(text_data=json.dumps({
            'type': 'raise_hand',
            'user': event['user'],
            'user_id': event['user_id'],
            'raised': event['raised']
        }))

    async def reaction(self, event):
        await self.send(text_data=json.dumps({
            'type': 'reaction',
            'user': event['user'],
            'user_id': event['user_id'],
            'emoji': event['emoji']
        }))

    async def question_ask(self, event):
        await self.send(text_data=json.dumps({
            'type': 'question_ask',
            'user': event['user'],
            'user_id': event['user_id'],
            'question': event['question'],
            'question_id': event['question_id']
        }))

    async def question_answered(self, event):
        await self.send(text_data=json.dumps({
            'type': 'question_answered',
            'user': event['user'],
            'user_id': event['user_id'],
            'question_id': event['question_id']
        }))

    async def poll_create(self, event):
        await self.send(text_data=json.dumps({
            'type': 'poll_create',
            'user': event['user'],
            'user_id': event['user_id'],
            'poll_id': event['poll_id'],
            'question': event['question'],
            'options': event['options']
        }))

    async def poll_vote(self, event):
        await self.send(text_data=json.dumps({
            'type': 'poll_vote',
            'user': event['user'],
            'user_id': event['user_id'],
            'poll_id': event['poll_id'],
            'option_index': event['option_index']
        }))

    async def poll_end(self, event):
        await self.send(text_data=json.dumps({
            'type': 'poll_end',
            'user': event['user'],
            'user_id': event['user_id'],
            'poll_id': event['poll_id']
        }))

    async def mic_control(self, event):
        await self.send(text_data=json.dumps({
            'type': 'mic_control',
            'user': event['user'],
            'user_id': event['user_id'],
            'target_user_id': event['target_user_id'],
            'enabled': event['enabled']
        }))

    @database_sync_to_async
    def add_participant(self):
        from django.contrib.auth.models import User
        from school.models import LiveSession, LiveParticipant
        try:
            # Get or create the live session
            session, created = LiveSession.objects.get_or_create(
                room_name=self.room_name,
                defaults={
                    'created_by': self.user,
                    'is_active': True,
                    'title': f'Live Session {self.room_name}'
                }
            )
            
            # Add participant
            participant, participant_created = LiveParticipant.objects.get_or_create(
                session=session,
                user=self.user,
                defaults={
                    'is_connected': True,
                    'is_video_on': True,
                    'is_audio_on': True
                }
            )
            
            if not participant_created:
                participant.is_connected = True
                participant.save()
                
            return session
        except Exception as e:
            print(f"Error adding participant: {e}")
            return None

    @database_sync_to_async
    def remove_participant(self):
        from django.contrib.auth.models import User
        from school.models import LiveSession, LiveParticipant
        try:
            participant = LiveParticipant.objects.get(
                session__room_name=self.room_name,
                user=self.user
            )
            participant.is_connected = False
            participant.save()
            
            # Check if session should be closed (no active participants)
            active_count = LiveParticipant.objects.filter(
                session__room_name=self.room_name,
                is_connected=True
            ).count()
            
            if active_count == 0:
                session = participant.session
                session.is_active = False
                session.save()
        except LiveParticipant.DoesNotExist:
            pass
        except Exception as e:
            print(f"Error removing participant: {e}")

    @database_sync_to_async
    def update_video_status(self, enabled):
        from django.contrib.auth.models import User
        from school.models import LiveSession, LiveParticipant
        try:
            participant = LiveParticipant.objects.get(
                session__room_name=self.room_name,
                user=self.user
            )
            participant.is_video_on = enabled
            participant.save()
        except LiveParticipant.DoesNotExist:
            pass
        except Exception as e:
            print(f"Error updating video status: {e}")

    @database_sync_to_async
    def update_audio_status(self, enabled):
        from django.contrib.auth.models import User
        from school.models import LiveSession, LiveParticipant
        try:
            participant = LiveParticipant.objects.get(
                session__room_name=self.room_name,
                user=self.user
            )
            participant.is_audio_on = enabled
            participant.save()
        except LiveParticipant.DoesNotExist:
            pass
        except Exception as e:
            print(f"Error updating audio status: {e}")