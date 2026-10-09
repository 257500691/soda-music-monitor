import unittest
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
from PIL import Image
from artwork import ArtworkSource, match_track, cover_urls
from album_theme import palette_for, luminance


def track(identity, title, artist, seconds=120):
    return {"id": identity, "name": title, "artists": [{"name": artist}], "duration": seconds*1000}


class ArtworkTests(unittest.TestCase):
    def test_same_name_artist_and_duration_select_correct_cover(self):
        first,second=track("1","Song","A"),track("2","Song","B")
        queue={"feed":{"playables":[first,second]}}
        self.assertIs(match_track(queue,"Song","B",120),second)
        self.assertIsNone(match_track(queue,"Song","A",190))

    def test_ambiguous_title_does_not_show_another_recording(self):
        queue={"feed":{"playables":[track("1","Song","A"),track("2","Song","A")]}}
        self.assertIsNone(match_track(queue,"Song","A",120))

    def test_unicode_apostrophe_does_not_break_matching(self):
        item=track("1","I Don’t Fold","Artist")
        self.assertIs(match_track({"feed":{"playables":[item]}},"I Don't Fold","Artist"),item)

    def test_cover_urls_use_image_template_and_ignore_other_hosts(self):
        urls=cover_urls({"urls":["https://p3-luna.douyinpic.com/img/"],"uri":"tos-cn/cover","template_prefix":"tplv-cover"})
        self.assertEqual(urls,["https://p3-luna.douyinpic.com/img/tos-cn/cover~tplv-cover-crop-center:720:720.jpg"])
        self.assertEqual(cover_urls({"urls":["file:///secret","https://example.com/picture"]}),[])

    def test_palette_changes_with_album_and_keeps_text_readable(self):
        image=Image.new("RGB",(64,64),"white")
        blue=palette_for(image,{"background_color":{"rgb":"214ca5"}})
        red=palette_for(image,{"background_color":{"rgb":"b33f43"}})
        self.assertNotEqual(blue["BG"],red["BG"])
        for palette in (blue,red,palette_for(image),palette_for(Image.new("RGB",(64,64),"black"))):
            for text in ("FG","MUTED","ACCENT"):
                self.assertGreaterEqual((luminance(palette[text])+.05)/(luminance(palette["BG"])+.05),4.5)

    def test_missing_metadata_extracts_the_actual_cover_color(self):
        blue=palette_for(Image.new("RGB",(64,64),"#104bd3"))
        red=palette_for(Image.new("RGB",(64,64),"#d31021"))
        self.assertGreater(int(blue["BG"][5:7],16),int(blue["BG"][1:3],16))
        self.assertGreater(int(red["BG"][1:3],16),int(red["BG"][5:7],16))

    def test_finished_request_cannot_replace_new_track_cover(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"preview.png"
            Image.new("RGB",(150,150),"red").save(path)
            source=ArtworkSource(directory,Path(directory)/"missing-queue")
            source.wanted=("New","Artist")
            source._load({"title":"Old","artist":"Artist","cover_path":str(path)},("Old","Artist"))
            self.assertIsNone(source.result)

    def test_unavailable_network_keeps_correct_system_cover(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"preview.png"
            Image.new("RGB",(150,150),"red").save(path)
            item=track("1","Song","Artist")
            item["cover_url"]={"urls":["https://p3-luna.douyinpic.com/img/"],"uri":"tos-cn/cover","template_prefix":"tplv-cover"}
            queue=Path(directory)/"queue.json"
            queue.write_text(json.dumps({"feed":{"playables":[item]}}),encoding="utf-8")
            source=ArtworkSource(directory,queue);source.wanted=("Song","Artist")
            with patch("artwork.urlopen",side_effect=OSError("offline")):
                source._load({"title":"Song","artist":"Artist","duration":120,"cover_path":str(path)},source.wanted)
            self.assertEqual(source.result["key"],("Song","Artist"))
            self.assertEqual(source.result["image"].getpixel((0,0)),(255,0,0))
            self.assertFalse(source.result["high_resolution"])


if __name__=="__main__":unittest.main()
