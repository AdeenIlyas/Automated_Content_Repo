import os
import json
import re
import time
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from pathlib import Path

import googleapiclient.discovery
import googleapiclient.errors
import yt_dlp
from dotenv import load_dotenv
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log
)
# New import for fallback transcript API
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound, VideoUnavailable

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Updated YouTube channels that post daily short AI news updates (under 30 minutes)
YOUTUBE_CHANNELS = [
    {
        "name": "The AI Daily Brief",
        "channel_id": "UCKelCK4ZaO6HeEI1KQjqzWA",
        "description": "Daily short videos covering AI news and trends"
    },
    {
        "name": "Artificial Intelligence News Daily",
        "channel_id": "UCItylrp-EOkBwsUT7c_Xkxg",
        "description": "Short daily updates on AI innovations and breakthroughs"
    }
]

class YouTubeAIScraper:
    def __init__(self, data_dir: str = "data/youtube"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        self.api_key = os.getenv("YOUTUBE_API_KEY")
        if not self.api_key:
            raise ValueError("YouTube API key not found in .env file")
        
        self.youtube = googleapiclient.discovery.build(
            "youtube", "v3", developerKey=self.api_key
        )
        
        self.ydl_opts = {
            'skip_download': True,
            'writesubtitles': True,
            'writeautomaticsub': True,
            'subtitleslangs': ['en'],
            'quiet': True,
            'no_warnings': True,
            'sleep_interval': 2,
            'max_sleep_interval': 5
        }

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((googleapiclient.errors.HttpError, Exception)),
        before_sleep=before_sleep_log(logger, logging.WARNING)
    )
    def get_channel_videos(self, channel_id: str, days_ago: int = 7) -> List[Dict[str, Any]]:
        """Retrieve videos from channel published in the last N days"""
        videos = []
        published_after = (datetime.utcnow() - timedelta(days=days_ago)).isoformat() + "Z"
        
        try:
            video_ids = []
            next_page_token = None
            
            # Paginate through all results
            while True:
                search_response = self.youtube.search().list(
                    part="snippet",
                    channelId=channel_id,
                    maxResults=50,
                    order="date",
                    publishedAfter=published_after,
                    type="video",
                    pageToken=next_page_token
                ).execute()
                
                video_ids.extend(item["id"]["videoId"] for item in search_response.get("items", []))
                next_page_token = search_response.get("nextPageToken")
                
                if not next_page_token:
                    break

            # Batch process video details
            for i in range(0, len(video_ids), 50):
                batch_ids = video_ids[i:i+50]
                videos_response = self.youtube.videos().list(
                    part="snippet,contentDetails,statistics",
                    id=",".join(batch_ids)
                ).execute()

                for item in videos_response.get("items", []):
                    duration = self._parse_duration(item["contentDetails"]["duration"])
                    if duration > 1800:  # 30-minute filter (1800 seconds)
                        continue
                    
                    video_data = {
                        "id": item["id"],
                        "title": item["snippet"]["title"],
                        "published_at": item["snippet"]["publishedAt"],
                        "description": item["snippet"]["description"],
                        "channel_title": item["snippet"]["channelTitle"],
                        "duration": duration,
                        "views": int(item["statistics"].get("viewCount", 0)),
                        "likes": int(item["statistics"].get("likeCount", 0)),
                        "url": f"https://youtube.com/watch?v={item['id']}"
                    }
                    videos.append(video_data)

            logger.info(f"Found {len(videos)} videos for channel {channel_id}")
            return videos

        except Exception as e:
            logger.error(f"Failed to fetch videos: {str(e)}")
            return []

    def _parse_duration(self, duration: str) -> int:
        """Convert ISO 8601 duration to total seconds"""
        match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration)
        hours = int(match.group(1)) if match.group(1) else 0
        minutes = int(match.group(2)) if match.group(2) else 0
        seconds = int(match.group(3)) if match.group(3) else 0
        return (hours * 3600) + (minutes * 60) + seconds

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type(yt_dlp.DownloadError),
        before_sleep=before_sleep_log(logger, logging.WARNING)
    )
    def get_video_transcript(self, video_id: str) -> Optional[str]:
        """Robust transcript extraction with multiple fallback methods"""
        try:
            transcript = None
            video_url = f"https://www.youtube.com/watch?v={video_id}"
            
            # Try yt_dlp with different subtitle sources
            for sub_source in ['subtitles', 'automatic_captions']:
                ydl_opts = self.ydl_opts.copy()
                ydl_opts['writesubtitles'] = (sub_source == 'subtitles')
                ydl_opts['writeautomaticsub'] = (sub_source == 'automatic_captions')
                
                try:
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(video_url, download=False)
                        transcript = self._extract_subtitles(info)
                        if transcript:
                            break
                except yt_dlp.DownloadError:
                    continue

            # First fallback: Try the built-in yt_dlp fallback method
            if not transcript:
                transcript = self._fallback_transcript_fetch(video_id)
            
            # Second fallback: Try using the youtube-transcript-api
            if not transcript:
                transcript = self._youtube_transcript_api_fetch(video_id)
            
            return transcript

        except Exception as e:
            logger.error(f"Failed to get transcript for {video_id}: {str(e)}")
            return None

    def _extract_subtitles(self, info: dict) -> Optional[str]:
        """Extract and combine subtitles from info dict"""
        subs_text = []
        for sub_type in ['subtitles', 'automatic_captions']:
            if info.get(sub_type) and 'en' in info[sub_type]:
                for line in info[sub_type]['en']:
                    if 'text' in line:
                        subs_text.append(line['text'])
        return ' '.join(subs_text) if subs_text else None

    def _fallback_transcript_fetch(self, video_id: str) -> Optional[str]:
        """Alternative transcript fetching method using yt_dlp with forcejson"""
        try:
            with yt_dlp.YoutubeDL({
                'skip_download': True,
                'forcejson': True,
                'quiet': True,
                'writesubtitles': True,
                'writeautomaticsub': True
            }) as ydl:
                info = ydl.extract_info(
                    f"https://youtube.com/watch?v={video_id}",
                    download=False
                )
                return self._extract_subtitles(info)
        except Exception as e:
            logger.warning(f"Fallback method using yt_dlp failed for {video_id}: {str(e)}")
            return None

    def _youtube_transcript_api_fetch(self, video_id: str) -> Optional[str]:
        """Fetch transcript using the youtube-transcript-api as an additional fallback."""
        try:
            transcript_data = YouTubeTranscriptApi.get_transcript(video_id, languages=['en'])
            transcript = " ".join([entry["text"] for entry in transcript_data])
            logger.info(f"Transcript fetched via youtube-transcript-api for {video_id}")
            return transcript
        except (TranscriptsDisabled, NoTranscriptFound, VideoUnavailable) as e:
            logger.warning(f"youtube-transcript-api could not fetch transcript for {video_id}: {str(e)}")
            return None
        except Exception as e:
            logger.error(f"An unexpected error occurred using youtube-transcript-api for {video_id}: {str(e)}")
            return None

    def process_channel(self, channel_info: Dict[str, str], days_ago: int = 7) -> List[Dict[str, Any]]:
        """Process all videos from a single channel"""
        results = []
        try:
            logger.info(f"Starting processing for {channel_info['name']}")
            videos = self.get_channel_videos(channel_info["channel_id"], days_ago)
            
            for video in videos:
                logger.info(f"Processing video: {video['title'][:50]}...")
                transcript = self.get_video_transcript(video["id"])
                
                if transcript:
                    video["transcript"] = transcript
                    results.append(video)
                    logger.info(f"Successfully processed {video['id']}")
                else:
                    logger.warning(f"No transcript found for {video['id']}")
                
                time.sleep(1)  # Rate limiting
            
            return results
        
        except Exception as e:
            logger.error(f"Failed to process channel {channel_info['name']}: {str(e)}")
            return []

    def save_results(self, results: List[Dict[str, Any]]) -> None:
        """Save collected data to JSON file"""
        if not results:
            logger.warning("No results to save")
            return
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = self.data_dir / f"ai_videos_{timestamp}.json"
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        
        logger.info(f"Saved {len(results)} videos to {output_file}")

    def run(self, days_ago: int = 7) -> None:
        """Main execution method"""
        all_results = []
        
        for channel in YOUTUBE_CHANNELS:
            try:
                logger.info(f"\n{'='*40}")
                logger.info(f"Processing channel: {channel['name']}")
                channel_results = self.process_channel(channel, days_ago)
                all_results.extend(channel_results)
                logger.info(f"Completed {channel['name']} with {len(channel_results)} videos")
                time.sleep(2)  # Channel processing cooldown
            except Exception as e:
                logger.error(f"Critical error processing {channel['name']}: {str(e)}")
                continue
        
        self.save_results(all_results)
        logger.info("Scraping completed successfully")

if __name__ == "__main__":
    try:
        print("YouTube AI Video Scraper")
        print("=" * 30)
        scraper = YouTubeAIScraper()
        scraper.run(days_ago=7)
        print("\nOperation completed successfully!")
    except KeyboardInterrupt:
        print("\nProcess interrupted by user")
    except Exception as e:
        print(f"\nCritical error occurred: {str(e)}")
