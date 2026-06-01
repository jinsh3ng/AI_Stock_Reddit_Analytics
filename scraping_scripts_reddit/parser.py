import json
from datetime import datetime


def extract_reddit_data(json_data):
    """Extract posts and comments with essential fields only"""
    
    all_records = []
    
    # Safely extract POST data
    try:
        post_listing = json_data[0]['data']['children'][0]['data']
        
        post_record = {
            'date': datetime.fromtimestamp(post_listing.get('created_utc', 0)).strftime('%Y-%m-%d'),
            'time': datetime.fromtimestamp(post_listing.get('created_utc', 0)).strftime('%H:%M:%S'),
            'url': f"https://www.reddit.com{post_listing.get('permalink', '')}",
            'subreddit': post_listing.get('subreddit_name_prefixed', ''),
            'content_type': 'forum_post',
            'author_handle': post_listing.get('author', ''),
            'title': post_listing.get('title', ''),
            'body': post_listing.get('selftext', ''),
            'upvotes': post_listing.get('ups', 0),
            'comment_count': post_listing.get('num_comments', 0)
        }
        
        all_records.append(post_record)
    except (KeyError, IndexError, TypeError) as e:
        print(f"Warning: Could not extract post data - {e}")
    
    # Extract COMMENTS data
    def parse_comment(comment_obj, depth=0):  # Added depth tracking for debugging
        """Parse comments with error handling"""
        try:
            # Skip 'more' objects
            if comment_obj.get('kind') != 't1':
                return
            
            comment_data = comment_obj.get('data', {})
            
            # Skip if no data or deleted/removed
            if not comment_data or comment_data.get('author') in ['[deleted]', '[removed]', None]:
                return
            
            comment_record = {
                'date': datetime.fromtimestamp(comment_data.get('created_utc', 0)).strftime('%Y-%m-%d'),
                'time': datetime.fromtimestamp(comment_data.get('created_utc', 0)).strftime('%H:%M:%S'),
                'url': f"https://www.reddit.com{comment_data.get('permalink', '')}",
                'subreddit': comment_data.get('subreddit_name_prefixed', ''),
                'content_type': 'forum_reply',
                'author_handle': comment_data.get('author', ''),
                'title': '',
                'body': comment_data.get('body', ''),
                'upvotes': comment_data.get('ups', 0),
                'comment_count': None
            }
            
            all_records.append(comment_record)
            
            # Process replies recursively
            replies = comment_data.get('replies')
            
            # Check if replies exists and is a dict
            if replies and isinstance(replies, dict):
                reply_children = replies.get('data', {}).get('children', [])
                for reply in reply_children:
                    parse_comment(reply, depth + 1)  # Recurse with depth tracking
                    
        except Exception as e:
            # Log unexpected errors but continue
            print(f"Error parsing comment at depth {depth}: {e}")
            pass
    
    # Parse all top-level comments
    try:
        if len(json_data) > 1:
            comments_listing = json_data[1].get('data', {}).get('children', [])
            for comment in comments_listing:
                parse_comment(comment, depth=0)
    except (KeyError, IndexError, TypeError) as e:
        print(f"Warning: Could not extract comments - {e}")
    
    return all_records


 