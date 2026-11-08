from sonos_control import views
import json
import os
import soco
import jsonpickle

class SoCoJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, soco.core.SoCo):
            return obj.player_name  # Serialize SoCo objects as their player name
        elif isinstance(obj, set):
            return list(obj)  # Convert sets to lists
        elif isinstance(obj, soco.groups.ZoneGroup):  # Handle ZoneGroup objects
            return [member.player_name for member in obj.members]
        elif isinstance(obj, soco.data_structures.DidlObject):  # Handle SoCo data structures
            return obj.to_dict()
        else:
            return super().default(obj)  # Let the base class default method raise the TypeError


def save_speaker_info_as_json(filename):
    speakers_info = views.sonos_get_speaker_info()

    # Get the directory of the current script
    current_directory = os.path.dirname(os.path.abspath(__file__))

    # Create the complete file path in the current directory
    file_path = os.path.join(current_directory, filename)

    # Save the speaker info as JSON in the current directory of the script
    with open(file_path, 'w') as json_file:
        json.dump(speakers_info, json_file, indent=4, cls=SoCoJSONEncoder)
    # with open(file_path, 'w') as json_file:
    #     json_data = jsonpickle.encode(speakers_info, indent=4)
    #     json_file.write(json_data)    

# Call the function to save the speaker info to a file
save_speaker_info_as_json('sonos_speakers_info.json')