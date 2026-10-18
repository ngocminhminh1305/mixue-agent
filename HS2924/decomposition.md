# Task Decomposition
This records my Task Decomposition intervention for the HS2924 Rewiring Project.

---

| Date | Duration | Section | Content|
| :--- | :--- | :--- | :--- |
| **27 Sep 2026** |~ 30 minutes| 1. Feature I will work on|**Daily sales image extraction**(the chosen feature to work on next week)<br>- Minimum: one sales image → extraction method → extracted output<br>- Extraction can have missing fields in same parts but should not be too bad|
|||2. Study / Research|1. Inspect one real sales image<br>2. Study the basic image → extraction → output process<br>3. Research possible extraction methods<br>4. Choose one baseline method<br>5. Research if there is any other method rather than OCR model (such as API)|
|||3. Build|1. Create extraction script<br>2. Build local frontend website<br>3. Load one sales image on the frontend to extract<br>4. Inspect the extract output<br>5. Interate the Extract process when the output is accepted (I expect that I will have to try at least 3-4 models, simple OCR did not work so need to find better model)|
|||4. Sequential Order|Inspect image→ Study extraction process→ Research methods→ Choose one method→ Create script→ Load image→ Set up extractor→ Run extraction→ Save result→ Compare result|
|||5. Closing prompt: What, if anything, feels clearer or more manageable after this session?|I hope that with a specific list of tasks to do, I can feel less overwhelmed when I work on the project. Because honestly, if I start on the project with an empty head of what to do, I feel a bit lazy to start on it. To conclude, the tasks to do on in the next week are clearer to me after this session|
| **04 Oct 2026** |11.06- 11.22pm|1. Feature I will work on|**Finish Model API to extract image** <br> **Implement database** <br> - Minimum: Model API extracts correctly, so focus on prompting, giving context for the model <br> **Shop data collection** <br> - Don't have to finish insert all data in, but need to send to mom data I want to collect, discuss with her everything about the data, It is good to have all data collected by this week |
|||2. Study / Research| 1. Find a free API model, the one with the best performance <br> 2. Restudy CS2102 to see relational database design, how to run SQL in Python app <br> 3. Restudy DSA2111 topics: How to containerize, docker for database, should I need non-relational database. It is the best to have relational database only, simplest setup |
|||3. Build| 1. Finish Model API, how to set up the API key <br> 2. Finish database implementation which means find the hosting app, where can I see the database schema, run query, insert|
|||4. Sequential Order|1. Find API model <br> 2. Implement API model in my app <br> 3. Restudy CS2102 <br> 4. Restudy DSA2111 <br> 5. Implement database|
|||5. Closing prompt: What, if anything, feels clearer or more manageable after this session?|- I feel like this is a very correct time to do this project, this semester I studied some non-required courses for my major, but I think those courses are relevant so I want to study. Those courses teach some topics really relevant to my project such as design the database, implement the database. So, I am very clear now on what to study, how to build <br> - Next week, I also have some business-related task is to ask my mom to help me collect data, so next week I can start inserting the data/transactions in my database|
| **11 Oct 2026** |10.52 - 11.08pm|1. Feature I will work on|**Inserting mom boba shop real recipes, inventory, product data into the database** <br> - Minimum: finish recipes, product data, inventory still need some checks from my mom <br> **Improve Model API reliability, need to extract 100% correct currently selling products and its quanity for the receipt date** <br> - Minimum: there is no minimum, since this task is to improve the extraction process|
|||2. Study / Research|1. Research/find youtube videos/project repo with improving model API reliability, did they apply NLP methods inside that or they just improve prompting sentence <br> 2. If need NLP methods, restudy related theory in DSA4213 and ask chatgpt for recommendations <br> 3. if need to improving prompting, learn how to better at engineering prompts|
|||3. Build|1. Biggest task of this week is to inserting my mum shop's data into the database|
|||4. Sequential Order|1. inserting data <br> 2. learn on how to improve model api reliability, performance <br> 3. watch youtube videos, find repo working on the same tasks <br> 4. Connect database and model api's reasoning|
|||5. Closing prompt: What, if anything, feels clearer or more manageable after this session?|- I'm clear on tasks I have to do, since the database was implemented already, I just have to insert data based on the database. I hope this would be manageable, although this would take a lot of time, and make sure there is no injections, which need evaluation and checking <br> - I'm also clear that I have to improve model api's performance, this app will failed if the model API extracts and identify the wrong product names|
| **18 Oct 2026** |11.00-11.07pm |1. Feature I will work on| - **Continue on writing sql queries to update database from sold products extracted from daily receipts**: this should be not too difficult <br> - **Reasearch if ediatable configuration page invokes injection**: I hope this would not invoke injection, if not I would be so sad to change the frontend again |
|||2. Study / Research|- restudy CS2101 the injection part, draw diagram to check if this would invoke injection <br> - research if invoke injection, what to do, restudy cs2102 the trigger part, maybe it would be beneficial (this is optional, I won't do this if it doesn't invoke injection)|
|||3. Build|- finish sql queries to update the inventory from extracted products, I expected this would take quite a long time|
|||4. Sequential Order|1. finish sql queries <br> 2. research injection <br> 3. if invoke injection <br> 4. if not invoke injection then can keep the current approach|
|||5. Closing prompt: What, if anything, feels clearer or more manageable after this session?|- I think my task decomposition is quite light, manageable since next week I have some assignments, so I will give myself less tasks, so I think I can finish all taks <br> - However, I hope that I can finish this project soon, so maybe next week I will have to ask more tasks|
| **25 Oct 2026** | |1. Feature I will work on| |
|||2. Study / Research||
|||3. Build||
|||4. Sequential Order||
|||5. Closing prompt: What, if anything, feels clearer or more manageable after this session?||
| **01 Nov 2026** | |1. Feature I will work on| |
|||2. Study / Research||
|||3. Build||
|||4. Sequential Order||
|||5. Closing prompt: What, if anything, feels clearer or more manageable after this session?||
| **08 Nov 2026** | |1. Feature I will work on| |
|||2. Study / Research||
|||3. Build||
|||4. Sequential Order||
|||5. Closing prompt: What, if anything, feels clearer or more manageable after this session?||


## Note:
1. My decomposition process: 
- I sketch tasks' mindmap/workflow for the following week on the paper/notetaking app with the help of AI/google/friends to ask about technical stuffs on how to do some specific features I chose. I then from the messy mindmap/workflow to a detailed, structured decomposition answering what I have to learn, research, then build
- I only use AI to asking technical stuffs, this file is 100% written by me
- The duration is longer than expected because typing on a markdown file is typing on the raw version, and I usually have to Ctrl+Shift+V to see the preview version (which at the end of the project, I will export to PDF, and PDF follows the preview version)
- I only timer the duration I was typing on this markdown file because it goes through the decomposition process I specified in the Rewiring Proposal, I would not timer the mindmap sketching process on paper
2. Prof's comment: `Record the actual time you spend on these practices during implementation.`