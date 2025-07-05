import os
import json
import re
from datetime import datetime, timedelta
from twscrape import API
import asyncio

def format_cookie(cookie_str: str) -> str:
    # Remove leading and trailing whitespace
    cookie_str = cookie_str.strip()
    # Replace newline characters with a space
    cookie_str = cookie_str.replace("\n", " ")
    # Replace multiple whitespace with a single space
    cookie_str = re.sub(r'\s+', ' ', cookie_str)
    return cookie_str

async def main():
    # Ask the user to input a cookie string.
    raw_cookie = input("Enter your cookie string:\n")
    cookie = format_cookie(raw_cookie)
    
    # Initialize the API and add an account with the provided cookie.
    api = API()
    await api.pool.add_account(
        username="", 
        password="", 
        email="",         # Update with your email if needed
        email_password="",     # Update with your email password if needed
        cookies=cookie
    )
    await api.pool.login_all()
    
    # Define Twitter profiles to scrape.
    profiles = ["AndrewYNg", "karpathy", "OpenAI", "ylecun", "demishassabis"]
    
    all_users = {}
    all_tweets = []
    
    # Define the date range (from last week until now).
    since_date = (datetime.utcnow() - timedelta(days=7)).strftime('%Y-%m-%dT%H:%M:%SZ')
    until_date = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
    
    for profile in profiles:
        query = f"from:{profile} since:{since_date} until:{until_date}"
        print(f"Scraping tweets for: {profile}")
        
        async for tweet in api.search(query):
            # Extract user data.
            user = tweet.user
            user_data = {
                "user_id": user.id,
                "username": user.username,
                "display_name": user.displayname,
                "followers_count": user.followersCount,
                "following_count": user.friendsCount
            }
            if user.id not in all_users:
                all_users[user.id] = user_data

            # Extract tweet data.
            tweet_data = {
                "tweet_id": tweet.id,
                "user_id": user.id,
                "content": tweet.rawContent,
                "published_at": tweet.date.isoformat(),
                "likes": tweet.likeCount,
                "retweets": tweet.retweetCount,
                "replies": tweet.replyCount,
                "url": f"https://twitter.com/{user.username}/status/{tweet.id}"
            }
            all_tweets.append(tweet_data)
    
    # Ensure the 'data' directory exists.
    data_dir = "data"
    os.makedirs(data_dir, exist_ok=True)
    
    # Save the scraped user and tweet data to JSON files with a timestamp.
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    users_file_path = os.path.join(data_dir, f"twitter_users_{timestamp}.json")
    tweets_file_path = os.path.join(data_dir, f"tweets_{timestamp}.json")
    
    with open(users_file_path, "w") as f:
        json.dump(list(all_users.values()), f, indent=4)
    with open(tweets_file_path, "w") as f:
        json.dump(all_tweets, f, indent=4)
    
    print(f"Scraped results saved to:\n{users_file_path}\n{tweets_file_path}")

if __name__ == '__main__':
    asyncio.run(main())

















































































