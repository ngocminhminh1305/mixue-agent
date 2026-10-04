# Progress Reflection 

This records my Progress Reflection intervention for the HS2924 Rewiring Project. The purpose of this intervention is to help me recognise concrete learning, partial progress, and effort even when a feature is unfinished, and to develop more realistic expectations about my personal project.

---

| Date | Duration | Section | Content|
| :--- | :--- | :--- | :--- |
| **27 Sep 2026** |~25 minutes|1. What did I attempt, and what happened?|I had not attempted any project feature recently. Today, I decided to restart the project from the beginning and focus on basic setup first. I completed:<br>- the project README<br>- the main project features and workflow<br>- the planned tech stack<br>- some SQL revision<br>- PostgreSQL installation<br>- initial research on how and where the project data should be stored.<br>For what I planned to do today, nothing remained unfinished. I did not feel particularly stressed or discouraged today because I decided to start the project again with a clearer direction. Moreover, I'm really determined to finish this project by the end of HS2924 Rewiring Project because I think this project is graded will somehow pushed me to work on it and this semester I studied a few courses about software engineering, database, containerization, deployment, cloud which are really relevant to my project, which I think I'm not unidirectional anymore.|
|||2. What progress did I make, and what effort can I acknowledge?|Starting again from scratch, so what I did lately basically is setup the project<br>1. About the progress I made:<br>- an updated README that gives me the new overview of the project<br>- clarified the main features I want to build<br>- clarified the tech stacks I am considering for the project<br>- also did some preparation for the database component by revising SQL, installing PostgreSQL, and researching data storage<br>2. What I learned / what became clearer:<br>- What became clearner is the revised scope of the project, after my summer internship and some conferences/hackathon I joined, I was thinking about increase the scope of this project with more required techical skills|
|||3. Were my expectations realistic, and what should I adjust?|- I did not expect to complete a technical feature in the previous week because I was restarting the project. My main expectation was to finish a README that gives me an overall view of the project and that I can refer back to while developing it<br>- For next week: I will work on the daily receipt extraction. There is nothing to adjust for now|
|||4. Closing prompt: What is different now compared with before this week’s work?|First week so there is nothing to write for the closing prompt|
| **04 Oct 2026** |10:06-10:33pm|1. What did I attempt, and what happened?|- I tried some OCR models that is not AI model API but was all failed, because OCR models try to recignize patterns and characters. However, the uploaded reciept image is in scan, which is less visible for pattern recognization. Therefore, all OCR models was failed<br> - I changed my approach, I don't use OCR anymore, I will use AI model API call then the AI model will capture the text. Model APIs in constrast uses contextual understanding to read and interpret then format the text as I want. And in this project, my Model API need to understand my mum boba shop's daily workflow and defined the rules for products, cup sizes, toppings, recipes, ingredient units, preparation yields and stock corrections, see the database then can read and capture what I want <br> - So basically, OCR and Model API are too different things. I have to change my approach, I don't start with extracting text in the image first, I have to do the setup of the database with tables and contexts inside, then Model API can retrive information from that, read the image, and reasoning then extracted information I want|
|||2. What progress did I make, and what effort can I acknowledge?|- Things I tried but failed: are implementing OCR models on my project <br> - Progress I made in the project: is gathering all business rules, recipes, data of my mom's boba shop, I then create the simple data schema so my mom can use <br> - Effort I can acknowledge: is the difference between ocr models and model APIs, which type of model to use in different case. And, I learnt that implemneting OCR is not possible for my project. So, for the same goal of extracting text from images, the approach is different based on the situation |
|||3. Were my expectations realistic, and what should I adjust?| - Honestly, this week is Week 7 in NUS, so I was busy with 3 midterms and some assignment. Therefore, I did not have much time to do this project. Therefore, I only just finished the database set up and found a new approach for my project which is Model API. However, I haven't implmented Model API. I will do it next week, but I have in mind what to do already. <br> - There is nothing I have to adjust at the moment. |
|||4. Closing prompt: What is different now compared with before this week’s work?|- I know that I have to change my approach: use Model APIs instead of OCR <br> - I also prepare the database schema, so in the next week, my Model API can understand the context and then extract text from image |
| **11 Oct 2026** ||1. What did I attempt, and what happened?||
|||2. What progress did I make, and what effort can I acknowledge?||
|||3. Were my expectations realistic, and what should I adjust?||
|||4. Closing prompt: What is different now compared with before this week’s work?||
| **18 Oct 2026** ||1. What did I attempt, and what happened?||
|||2. What progress did I make, and what effort can I acknowledge?||
|||3. Were my expectations realistic, and what should I adjust?||
|||4. Closing prompt: What is different now compared with before this week’s work?||
| **25 Oct 2026** ||1. What did I attempt, and what happened?||
|||2. What progress did I make, and what effort can I acknowledge?||
|||3. Were my expectations realistic, and what should I adjust?||
|||4. Closing prompt: What is different now compared with before this week’s work?||
| **01 Nov 2026** ||1. What did I attempt, and what happened?||
|||2. What progress did I make, and what effort can I acknowledge?||
|||3. Were my expectations realistic, and what should I adjust?||
|||4. Closing prompt: What is different now compared with before this week’s work?||
| **08 Nov 2026** ||1. What did I attempt, and what happened?||
|||2. What progress did I make, and what effort can I acknowledge?||
|||3. Were my expectations realistic, and what should I adjust?||
|||4. Closing prompt: What is different now compared with before this week’s work?||


## Note:
- Writing duration is longer than expected because I was typing on markdown file which is not simple in the interface-wise compared to google docs, I have to use symbol to go to the next line `<br>`, create table `|||||`, etc
- This file is all written by me from scratch
- Prof's comment: `Record the actual time you spend on these practices during implementation.` I will see the time I start writing and the time I finish writing