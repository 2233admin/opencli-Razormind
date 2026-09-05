import argparse
import sys


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    parser = argparse.ArgumentParser()
    parser.add_argument('username')  # Instagram username (without @)
    args = parser.parse_args()

    js = f"""
    (async function() {{
      try {{
        var r = await fetch(
          'https://www.instagram.com/api/v1/users/web_profile_info/?username={args.username}',
          {{
          headers: {{
            'X-IG-App-ID': '936619743392459',
            'X-Requested-With': 'XMLHttpRequest'
          }}
        }});
        if (!r.ok) return JSON.stringify({{ error: true, message: 'HTTP ' + r.status }});
        var data = await r.json();
        var u = data.data && data.data.user;
        if (!u) return JSON.stringify({{ error: true, message: 'User not found' }});
        return JSON.stringify({{
          id: u.id,
          username: u.username,
          full_name: u.full_name,
          biography: u.biography,
          follower_count: u.edge_followed_by && u.edge_followed_by.count,
          following_count: u.edge_follow && u.edge_follow.count,
          media_count: u.edge_owner_to_timeline_media && u.edge_owner_to_timeline_media.count,
          is_verified: u.is_verified,
          is_business_account: u.is_business_account,
          profile_pic_url: u.profile_pic_url,
          external_url: u.external_url || null,
          is_private: u.is_private
        }});
      }} catch(e) {{
        return JSON.stringify({{ error: true, message: e.message }});
      }}
    }})()
    """
    print(js)

if __name__ == '__main__':
    main()
