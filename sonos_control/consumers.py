# sonos_control/consumers.py

import json
from channels.generic.websocket import AsyncWebsocketConsumer
from .views import sonos_get_speaker_info, adjust_speaker_volume, speaker_play_pause, sonos_clear_queue
import asyncio

class SonosConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        await self.accept()
        self.speaker_group_name = 'sonos_speakers'

        # Add the WebSocket to the group
        await self.channel_layer.group_add(
            self.speaker_group_name,
            self.channel_name
        )

        # Initialize the previous state to None
        self.previous_speaker_data = None

        # Start the task to send updates periodically, only if not already running
        if not hasattr(self, 'send_task') or self.send_task.done():
            self.send_task = asyncio.create_task(self.send_speaker_updates())

    async def disconnect(self, close_code):
        # Leave the group
        await self.channel_layer.group_discard(
            self.speaker_group_name,
            self.channel_name
        )

        # Cancel the periodic task if it exists
        if hasattr(self, 'send_task') and not self.send_task.done():
            try:
                self.send_task.cancel()
            except asyncio.CancelledError:
                pass

    async def receive(self, text_data):
        # Parse the incoming WebSocket message (which is a JSON string)
        data = json.loads(text_data)
        
        # Extract the relevant fields from the data
        action = data.get('action')
        
        if action == 'volume':
            speaker_uid = data.get('speaker_uid')
            volume = data.get('volume')            
            # Call the shared function to adjust the volume
            result = adjust_speaker_volume(speaker_uid, volume)

            # Send the result back to the WebSocket client
            await self.send(text_data=json.dumps({
                'status': result['status'],
                'message': result.get('message'),
                'action': 'volume',
                'type': 'response'
            }))
        elif action == 'play_track':
            speaker_uid = data.get('speaker_uid')
            track_index = data.get('track_index')            
            # play the track on the speaker
            result = speaker_play_pause(speaker_uid, "play_track", track_index)

            # Send back a response to the client
            await self.send(text_data=json.dumps({
                'status': result['status'],
                'message': result['message'],
                'speaker_uid': speaker_uid,
                'action': 'play_track',
                'type': 'response'
            }))
        elif action == 'pause':
            print("Pause action received")
            speaker_uid = data.get('speaker_uid')
            # pause the speaker
            result = speaker_play_pause(speaker_uid, "pause")

            # Send back a response to the client
            await self.send(text_data=json.dumps({
                'status': result['status'],
                'message': result['message'],
                'speaker_uid': speaker_uid,
                'action': 'pause',
                'type': 'response'
            }))
        elif action == 'play':
            print("Play action received")
            speaker_uid = data.get('speaker_uid')
            # Call your logic to play the speaker
            result = speaker_play_pause(speaker_uid, "play")
            
            if result['status'] != 'success':
                print(f"Error: {result['message']}")
                # Send back a response to the client
            
            await self.send(text_data=json.dumps({
                'status': result['status'],
                'message': result['message'],
                'speaker_uid': speaker_uid,
                'action': 'play',
                'type': 'response'
            }))
        elif action == 'clear_queue':
            print("Clear queue action received")
            speaker_uid = data.get('speaker_uid')
            # Call your logic to clear the queue
            result = sonos_clear_queue(speaker_uid)

            # Send back a response to the client
            await self.send(text_data=json.dumps({
                'status': result['status'],
                'message': result['message'],
                'speaker_uid': speaker_uid,
                'action': 'clear_queue',
                'type': 'response'
            }))
        else:
            await self.send(text_data=json.dumps({
                'status': 'error',
                'message': 'Unknown action',
                'type': 'response'
            }))

    async def send_speaker_updates(self):
        while True:
            # Fetch the latest speaker info
            speakers_info = sonos_get_speaker_info()

            # Prepare the new data to send
            speaker_data = {}
            for info in speakers_info:
                if info['is_coordinator']:
                    speaker_data[info['uid']] = {
                        'group_label': info['group_label'],
                        'track': info['track'],
                        'artist': info['artist'],
                        'album': info['album'],
                        'volume': info['volume'],
                        'play_state': info['play_state'],
                        'album_art': info['album_art'],
                        'queue': info['queue']
                    }

            # Check if the data has changed
            if speaker_data != self.previous_speaker_data:
                # Data has changed, send it to the WebSocket
                
                #print play_state for each speaker
                for speaker in speaker_data:
                    print(f"{speaker}: {speaker_data[speaker]['play_state']}")
                
                await self.send(text_data=json.dumps({
                    'type': 'speaker_update',
                    'speaker_data': speaker_data
                }))
                # Update the previous state
                self.previous_speaker_data = speaker_data

            # Wait for 1 seconds before checking again
            await asyncio.sleep(1)
            
    async def adjust_speaker_volume(self, speaker_name, volume):
        # This function should contain the logic to adjust the speaker's volume
        # using your existing Sonos integration or other systems.
        
        # Placeholder logic: Just print for now
        print(f"Adjusting {speaker_name}'s volume to {volume}")
        
        # Here you'd add the actual volume adjustment logic, for example:
        # speaker = get_speaker_by_name(speaker_name)
        # speaker.set_volume(volume)
        # Adjust this according to your Sonos control logic            
