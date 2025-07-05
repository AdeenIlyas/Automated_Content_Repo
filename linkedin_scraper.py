import os
import json
import re
from datetime import datetime, timedelta
from linkedin_api import Linkedin
from dotenv import load_dotenv

load_dotenv()

AI_PROFILES = [
    "andrewyng",
    "aravind-srinivas-16051987",
    "malan"
]

class LinkedInScraper:
    def __init__(self, email, password):
        self.api = Linkedin(email, password)
    
    def parse_relative_time(self, time_str):
        """Improved relative time parser with error handling"""
        try:
            match = re.match(r'(\d+)([hdwmo]+)', time_str)
            if not match:
                return None
                
            value, unit = match.groups()
            value = int(value)
            
            if 'h' in unit:
                return datetime.now() - timedelta(hours=value)
            if 'd' in unit:
                return datetime.now() - timedelta(days=value)
            if 'w' in unit:
                return datetime.now() - timedelta(weeks=value)
            if 'mo' in unit:
                return datetime.now() - timedelta(days=value*30)  # Approximate
            return None
        except Exception as e:
            print(f"Error parsing relative time '{time_str}': {str(e)}")
            return None

    def get_posts_by_date(self, public_id, days=7):
        cutoff_date = datetime.now() - timedelta(days=days)
        posts_data = []
        
        try:
            posts = self.api.get_profile_posts(public_id=public_id, post_count=20)
            if not posts:
                return []

            for i, post in enumerate(posts):
                try:
                    # 1. Primary timestamp extraction
                    post_timestamp = (
                        post.get('updateMetadata', {}).get('createdAt') or
                        post.get('socialDetail', {}).get('timestamp') or
                        post.get('published') or
                        post.get('createdAt') or
                        post.get('value', {}).get('com.linkedin.voyager.feed.render.UpdateV2', {}).get('publishedAt')
                    )

                    # 2. Fallback to relative time parsing
                    if not post_timestamp and 'actor' in post:
                        time_str = post['actor'].get('subDescription', {}).get('text', '').split('•')[0].strip()
                        post_date = self.parse_relative_time(time_str)
                    else:
                        # Convert numeric timestamps
                        if isinstance(post_timestamp, int):
                            divisor = 1000 if post_timestamp > 1e12 else 1
                            post_date = datetime.fromtimestamp(post_timestamp/divisor)
                        elif isinstance(post_timestamp, str):
                            post_date = datetime.fromisoformat(post_timestamp.replace('Z', '+00:00'))

                    # 3. Final validation
                    if not post_date or post_date < cutoff_date:
                        continue

                    # Extract content
                    content = "No content"
                    if 'commentary' in post:
                        content = post['commentary'].get('text', post['commentary']) if isinstance(post['commentary'], dict) else post['commentary']

                    posts_data.append({
                        "id": post.get('urn', f"unknown_{i}").split(':')[-1],
                        "content": content,
                        "published": post_date.isoformat(),
                        "url": f"https://linkedin.com/feed/update/urn:li:activity:{post.get('urn', '').split(':')[-1]}"
                    })

                except Exception as e:
                    print(f"Error processing post {i}: {str(e)}")
                    continue

            return posts_data

        except Exception as e:
            print(f"Error fetching posts: {str(e)}")
            return []

def main():
    scraper = LinkedInScraper(os.getenv("LINKEDIN_EMAIL"), os.getenv("LINKEDIN_PASSWORD"))
    os.makedirs("data", exist_ok=True)

    results = {
        "scrape_date": datetime.now().isoformat(),
        "profiles": {pid: {"posts": []} for pid in AI_PROFILES},
        "total_posts": 0
    }

    for pid in AI_PROFILES:
        posts = scraper.get_posts_by_date(pid)
        results["profiles"][pid]["posts"] = sorted(posts, key=lambda x: x['published'], reverse=True)
        results["profiles"][pid]["post_count"] = len(posts)
        results["total_posts"] += len(posts)

    filename = f"data/linkedin_ai_updates_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(filename, 'w') as f:
        json.dump(results, f, indent=2)

    print(f"\nSuccessfully saved {results['total_posts']} posts to {filename}")

if __name__ == "__main__":
    main()