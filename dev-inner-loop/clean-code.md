Unless the user asks for this explcility, do the following only for new code, DO NOT change places you are not working on.

Before making a change, see if a refactor would make the code better. If so, ask user if they want to refactor the code first, then commit it, to keep the commits clean
Keep code DRY (Don't repeat yourself)
When you find bugs, add unit tests that are red, then turn them grean.
Avoid nesting scopes, try to minimize telescoping
Leave functions as soon as you can. (Don't apply a comment for that)
Use const whenever you can
Use types whenever you can
Use data objects when you can POJO/POCO, or pydantic objects
Use humble objects (called a manager) when interacting with external systems, if there isn't one, ask user if they want one - This makes testing easier - This makes it easy to test business logic
