import argparse
import json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--sort',
        default='',
        help=(
            'Sort option: "" (default), "reduce" (price-drop), '
            '"create" (newest), "price-asc", "price-desc"'
        ),
    )
    parser.add_argument('--publish-days', default='',
                        help='Filter by publish date: "" (all), "1", "3", "7", "14"')
    parser.add_argument('--price-min', default='', help='Min price filter (e.g., 500)')
    parser.add_argument('--price-max', default='', help='Max price filter (e.g., 5000)')
    args = parser.parse_args()

    # Build searchFilter string
    search_filter_parts = []
    if args.publish_days:
        search_filter_parts.append(f'publishDays:{args.publish_days};')
    if args.price_min and args.price_max:
        search_filter_parts.append(f'priceRange:{args.price_min},{args.price_max};')
    search_filter = ''.join(search_filter_parts)

    # Resolve sort field/value
    sort_map = {
        '': ('', ''),
        'reduce': ('reduce', 'desc'),
        'create': ('create', 'desc'),
        'price-asc': ('price', 'asc'),
        'price-desc': ('price', 'desc'),
    }
    sort_field, sort_value = sort_map.get(args.sort, ('', ''))

    js = f"""
    (function() {{
      try {{
        var sortField = {json.dumps(sort_field)};
        var sortValue = {json.dumps(sort_value)};
        var searchFilter = {json.dumps(search_filter)};
        var priceMin = {json.dumps(args.price_min)};
        var priceMax = {json.dumps(args.price_max)};

        // Apply sort if needed
        if (sortField === 'reduce') {{
          // Click 新降价 direct button
          var sortBtns = document.querySelectorAll('[class*="search-select-title"]');
          for (var i = 0; i < sortBtns.length; i++) {{
            if (sortBtns[i].textContent.trim() === '新降价') {{
              sortBtns[i].click();
              break;
            }}
          }}
        }} else if (sortField === 'create') {{
          // Click 最新 inside 新发布 dropdown
          var items = document.querySelectorAll('[class*="search-select-item"]');
          for (var i = 0; i < items.length; i++) {{
            if (items[i].textContent.trim() === '最新') {{
              items[i].click();
              break;
            }}
          }}
        }} else if (sortField === 'price' && sortValue === 'asc') {{
          var items = document.querySelectorAll('[class*="search-select-item"]');
          for (var i = 0; i < items.length; i++) {{
            if (items[i].textContent.trim() === '价格从低到高') {{
              items[i].click();
              break;
            }}
          }}
        }} else if (sortField === 'price' && sortValue === 'desc') {{
          var items = document.querySelectorAll('[class*="search-select-item"]');
          for (var i = 0; i < items.length; i++) {{
            if (items[i].textContent.trim() === '价格从高到低') {{
              items[i].click();
              break;
            }}
          }}
        }}

        // Apply publishDays filter (inside 新发布 dropdown)
        if (searchFilter.indexOf('publishDays') >= 0) {{
          var dayMatch = searchFilter.match(/publishDays:(\\d+)/);
          if (dayMatch) {{
            var dayLabel = dayMatch[1] === '1' ? '1天内' : (dayMatch[1] + '天内');
            var items = document.querySelectorAll('[class*="search-select-item"]');
            for (var i = 0; i < items.length; i++) {{
              if (items[i].textContent.trim() === dayLabel) {{
                items[i].click();
                break;
              }}
            }}
          }}
        }}

        // Apply price range filter
        if (priceMin && priceMax) {{
          var inputs = document.querySelectorAll('[class*="search-price-input"] input');
          if (inputs.length >= 2) {{
            var setter = Object.getOwnPropertyDescriptor(
              window.HTMLInputElement.prototype, 'value'
            ).set;
            setter.call(inputs[0], priceMin);
            inputs[0].dispatchEvent(new Event('input', {{bubbles: true}}));
            setter.call(inputs[1], priceMax);
            inputs[1].dispatchEvent(new Event('input', {{bubbles: true}}));
            var confirmBtn = document.querySelector('[class*="search-price-confirm-button"]');
            if (confirmBtn) confirmBtn.click();
          }}
        }}

        return JSON.stringify({{ ok: true, applied: {{
          sort: sortField + (sortValue ? ':' + sortValue : ''),
          searchFilter: searchFilter
        }} }});
      }} catch(e) {{
        return JSON.stringify({{ error: true, message: e.message }});
      }}
    }})()
    """
    print(js)

if __name__ == '__main__':
    main()
