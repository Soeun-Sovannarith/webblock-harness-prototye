# **WebBlock: Product & Technical Specification (Draft)**

AI-Powered No-Code Website Builder for Small Businesses

Date: 16-10-2026  
Document Status: Draft  
Project: WebBlock  
Document Type: Feature Detail

**Team & Contributors**

* **Team Members:** Soeun Sovannarith, Tha Lina, Uy Chakriya,   
  Roern Chamreun, Ong Endy, Loem Devid, Chiv Lyhov   
* **Mentors:** Vey Seavlang, Meng Sothearith  
* **Advisors:** Chhun Rotanakkosal, Kong Vungsovanreach

**Contents**

[**1\. Overview	2**](#1.-overview)

[**2\. Users & Roles	2**](#2.-users-&-roles)

[2.1. Target Users \- Small Business Owners	2](#2.1.-target-users---small-business-owners)

[2.2. Admin (WebBlock Platform Administrator)	2](#2.2.-admin-\(webblock-platform-administrator\))

[2.3. User (Business Owner)	3](#2.3.-user-\(business-owner\))

[2.4. System (Automated)	3](#2.4.-system-\(automated\))

[**3\. Core Features	4**](#3.-core-features)

[3.1. Authentication	5](#3.1.-authentication)

[3.2. Dashboard (Template selection inside this)	6](#3.2.-dashboard-\(template-selection-inside-this\))

[3.3. Website Creation	7](#3.3.-website-creation)

[3.4. Visual Website Editor (Puck) \+ Preview	7](#3.4.-visual-website-editor-\(puck\)-+-preview)

[3.5. Domain & SSL Management	8](#3.5.-domain-&-ssl-management)

[3.6. AI Website Generation	9](#3.6.-ai-website-generation)

[3.7. Deployment (CI/CD)	10](#3.7.-deployment-\(ci/cd\))

[3.8. Website Management	11](#3.8.-website-management)

[3.9. Payment Service	12](#3.9.-payment-service)

[3.10. Admin Portal (WebBlock Admin)	13](#3.10.-admin-portal-\(webblock-admin\))

[3.11. Notifications	14](#3.11.-notifications)

[**4\. Feature-to-Workflow Mapping	15**](#4.-feature-to-workflow-mapping)

# 

# 

# **1\. Overview** {#1.-overview}

WebBlock is an AI-powered no-code website builder that helps small businesses create, customize, deploy, and manage professional websites without coding. It provides templates, visual customization, responsive previews, and managed hosting and deployment, while excluding some features within the user’s generated website, such as online payments, orders and bookings. The only payment WebBlock handles is the business owner's monthly subscription to WebBlock itself (Feature 09).

# **2\. Users & Roles** {#2.-users-&-roles}

## **2.1. Target Users \- Small Business Owners** {#2.1.-target-users---small-business-owners}

Examples include:

* Restaurants  
* Cafés  
* Salons  
* Barbershops  
* Fitness businesses  
* Service businesses  
* Small retail businesses  
* Personal businesses  
* Local professional services  
* Other businesses requiring an informational or promotional website

## **2.2. Admin (WebBlock Platform Administrator)** {#2.2.-admin-(webblock-platform-administrator)}

Platform administrators operate the WebBlock platform itself. They sign in with the email and password provided by the developer and are responsible for:

* Managing user accounts  
* Managing user websites, including website status and deploy results  
* Redeploying a website when needed  
* Managing user subscriptions  
* Managing plans and pricing (Plus, Pro, Ultra)  
* Creating offers (discounts or free trials)

Administrators work in the Admin Portal (Feature 10), which is separate from the business owner’s Dashboard and Business Portal.

## **2.3. User (Business Owner)** {#2.3.-user-(business-owner)}

The business owner is the User role. Each business owner can:

* Create and edit websites in the visual website editor  
* Choose the website’s WebBlock subdomain when the website is created (it cannot be changed afterwards)  
* Deploy and manage their websites  
* Use the free trial and subscribe to a paid plan  
* Manage products and business information in the Business Portal (the management area of each generated website, opened from Website Management, Feature 08), on the web or in the mobile app (Android \+ iOS)  
* Receive notifications about their apps, payments (their subscription payment to WebBlock), and resources

## **2.4. System (Automated)** {#2.4.-system-(automated)}

The system is not a person and has no login, so it is not a user role in the same sense as Admin and User. It is the name this document uses for the parts of WebBlock that run automatically, with no user action, after a user triggers them: AI Website Generation (Feature 06), Deployment (CI/CD) (Feature 07\) and Notifications (Feature 11).

# 

# 

# **3\. Core Features**  {#3.-core-features}

The WebBlock MVP is made up of 11 features. Each feature below lists the roles involved, its sub-features, and what each role can do. The roles are Admin (WebBlock platform administrator), User (business owner) and System (runs automatically, with no user action).

| \# | Feature | Roles | Summary |
| :---- | :---- | :---- | :---- |
| 01 | Authentication | Admin, User | Register and sign in as admin or user. |
| 02 | Dashboard (Template selection inside this) | User | Home screen to open existing websites, start a new one from a template, manage settings and contact support |
| 03 | Website Creation | User | Fill in business information to start a website |
| 04 | Visual Website Editor (Puck) \+ Preview | User | Edit blocks, content and design, then preview |
| 05 | Domain & SSL Management | System, User | Give each website its own WebBlock subdomain, secured with SSL (HTTPS) |
| 06 | AI Website Generation | System | Turn the Puck layout into Next.js code |
| 07 | Deployment (CI/CD) | System | Containerize and deploy the app to production |
| 08 | Website Management | User | Edit, preview and redeploy a live site, and manage its products and business information in the Business Portal |
| 09 | Payment Service | User | Free trial, then monthly paid plans |
| 10 | Admin Portal (WebBlock Admin) | Admin | WebBlock admin manages user accounts, websites and deployment logs, plus subscriptions, plans, pricing and offers |
| 11 | Notifications | System | Web, mobile and email alerts for app, payment, and resource events |

## **3.1. Authentication** {#3.1.-authentication}

Authentication controls who can enter WebBlock and which area each person reaches. Business owners create their own account, either with a Google account or with an email address that is verified by a one-time password (OTP), and then sign in to reach their Dashboard. 

WebBlock system admins do not register themselves; they sign in with the account provided by the development team and are taken to the Admin Portal.

**Roles:** **Admin** (WebBlock system admin), **User** (business owner)

**Sub-features**

* **Registration (Google or email)**  
  * **User**: register in one step with a Google account or register by email by entering a full name and email address  
* **Email OTP verification**  
  * **User**: receive a one-time password (OTP) at the email address used for registration  
  * **User**: enter the OTP to verify the email, then create and confirm a password to finish the account  
* **Sign in**  
  * **User**: sign in with email and password, or continue with Google  
  * **Admin** (WebBlock system admin): sign in with the email and password provided by the developer; admin accounts cannot be created through registration  
* **Forgot password**  
  * **User**: request a password reset from the sign-in screen  
  * **User**: receive a 6-digit OTP by email and enter it to confirm the request  
  * **User**: set and confirm a new password, then sign in with it

## **3.2. Dashboard (Template selection inside this)** {#3.2.-dashboard-(template-selection-inside-this)}

The Dashboard is the business owner’s home screen after signing in. From here, the owner can see and reopen the websites they have already created, choose a template to start a new website, manage their own account settings, and contact the WebBlock team when they need help. For fresh users, we will push them to the template first since they don’t have any existing website to view yet. 

**Roles:** User

**Sub-features**

* **My Websites**  
  * View the list of all websites the user has created  
  * Search the list to quickly find a specific website  
  * When a new owner signs in for the first time, this list is empty and the Dashboard guides them to Template Selection to start their first website  
  * Open a selected website in the Visual Website Editor (Puck) to continue working on it  
* **Template Selection**  
  * Browse the website templates that WebBlock provides (3 templates in the MVP)  
  * Search the templates to find one that fits the business  
  * Select a template to start a new website; this opens the business information form (Feature 03\)  
* **Settings**  
  * View the account’s full name and email  
  * Update the full name and email  
  * Change the account password  
  * Log out of WebBlock  
* **Contact Support**  
  * Write and send an email to the WebBlock admin to ask a question or report a problem

## **3.3. Website Creation** {#3.3.-website-creation}

Website Creation starts right after the business owner selects a template on the Dashboard. A form asks for the basic information about the business, such as its name, type, contact details and opening hours. Once the form is complete, the owner continues to the Visual Website Editor to build the website.

**Roles:** User

**What the user does:** 

1. Select a template on the Dashboard; the business information form opens  
2. Enter the business name  
3. Choose the business type from the drop-down list  
4. Write a short business description  
5. Upload a business logo (optional)  
6. Enter the contact details: phone number and email  
7. Enter the business address and opening hours  
8. Complete the form and continue to the Visual Website Editor (Puck)

## **3.4. Visual Website Editor (Puck) \+ Preview** {#3.4.-visual-website-editor-(puck)-+-preview}

The Visual Website Editor, built on Puck, is where the business owner designs the website without writing code. The owner builds each page from predefined blocks by dragging and dropping them, changes the text and images inside each block, and adjusts the layout and visual style. Work can be saved as a draft at any time, and the Preview shows how the website will look on desktop, tablet and mobile before it is published.

**Roles:** User

**Sub-features**

* **Manage components**  
  * Add a block by dragging it from the component list or tapping it  
  * Remove or duplicate a block  
  * Reorder blocks by dragging them on the page or in the sections list  
  * View the page structure in the sections list  
  * Use predefined blocks only: Hero, Text, Product Cards, About, Location, Footer  
* **Customize content**  
  * Edit headings, descriptions, welcome messages and button text  
  * Edit paragraphs in supported blocks such as About, Text and Footer  
  * Replace supported images, such as the Hero image  
* **Layout and design**  
  * Set the number of Product Card columns (2–4)  
  * Set the content alignment (left, center, right) and spacing (tight, normal, roomy)  
  * Choose a color palette (accent, background, text)  
  * Choose a font style (Editorial, Clean, Rounded) and a button style (square, rounded, pill)  
* **Save & draft**  
  * Manually save changes as a draft and continue later  
  * Undo and redo changes while editing  
* **Preview**  
  * Preview the website in desktop, tablet and mobile views  
  * Return to the Puck editor if changes are needed  
  * Choose the WebBlock subdomain name before the first deployment  
  * Proceed to publishing after reviewing the result

## **3.5. Domain & SSL Management** {#3.5.-domain-&-ssl-management}

Every website published on WebBlock is reached through its own address under the WebBlock domain, called a subdomain (for example, business.webblock.com). The business owner chooses this name, and the system then secures it with an SSL certificate so the website opens over HTTPS and connects it to the deployed website. In the MVP, owners cannot connect their own custom domain (such as business.com).

**Roles:** System, User

**Sub-features**

* **WebBlock subdomain (no custom domains)**  
  * **User**: choose a subdomain name before the first deployment (e.g., business.webblock.com)  
  * **User**: view the subdomain name later from Website Management (Feature 08); it cannot be changed once chosen  
  * Only WebBlock subdomains are available; custom domains are not supported in the MVP  
* **SSL certificates**  
  * **System**: issue an SSL certificate automatically for every subdomain, so the website is served securely over HTTPS  
  * **System**: renew each certificate automatically, with no action needed from the user  
* **Subdomain mapping**  
  * **System**: map each subdomain to the infrastructure of its deployed website, so visitors who open the address reach the right site

## **3.6. AI Website Generation** {#3.6.-ai-website-generation}

AI Website Generation is the step that turns the website the owner designed into a real, working web application. When the owner publishes, the Puck editor has already saved the page as a layout: a structured description of which blocks are used, in what order, and with what content and design settings. The system reads this layout and generates the source code of a Next.js application. This includes the frontend pages that visitors see and the backend that stores and serves the business’s data, such as its products. The Business Portal itself is also generated along the way with the user’s website, and its backend. After that, the generated application is then handed to Deployment (CI/CD) (Feature 07\) to go live.

**Roles:** System (runs automatically)

**Sub-features**

* **Layout to code**  
  * Read the PuckJS layout saved by the editor: the list of blocks, their order, their content and their design settings  
  * Convert the PuckJS layout into Next.js code, so each block (Hero, Text, Product Cards, About, Location, Footer) becomes part of a real page  
  * Keep the design the owner chose in the editor, such as colors, fonts, button style, alignment and spacing, so the generated website matches the Preview

* **Backend code**  
  * Generate the backend code from a predefined project structure, so every generated website follows the same organisation of files and code  
  * Generate the backend database schema from the custom fields, so the business’s data is stored with the fields it needs  
  * Produce Next.js API route handlers that run CRUD (create, read, update, delete) operations on the shared database, including the custom fields  
  * Through these API routes, the website’s data, such as products, can be created, read, updated and deleted

## **3.7. Deployment (CI/CD)** {#3.7.-deployment-(ci/cd)}

Deployment (CI/CD) is the automated pipeline that takes the code produced by AI Website Generation (Feature 06\) and puts it live on the internet. The pipeline packages the application into a Docker container, creates the infrastructure that this website needs to run, and deploys both the frontend and the backend/API. Each generated website runs on its own independent infrastructure. When the deployment finishes, the website is reachable on its WebBlock subdomain, and the owner is told whether the deployment succeeded or failed. If the deployment fails, the users are able to retry the deployment twice. 

**Roles:** System (runs automatically, no user action)

**Sub-features**

* **Deployment trigger**  
  * Start the deployment of the generated app to production automatically when the owner publishes from the Preview, or deploys the latest changes from Website Management (Feature 08\)  
  * Start a redeploy when a WebBlock system admin redeploys a website from the Admin Portal (Feature 10\)  
* **Containerization**  
  * Containerize the generated application using Docker, packaging the code and everything it needs to run into one container image  
  * The same container runs in the same way wherever it is deployed  
* **Infrastructure provisioning**  
  * Provision the generated app’s independent infrastructure dynamically via the deploy management framework  
  * Keep each website on its own infrastructure, separate from the other websites on WebBlock  
* **Frontend and backend/API deploy**  
  * Deploy the Next.js frontend and the backend/API to the provisioned infrastructure  
  * Connect the deployed website to its WebBlock subdomain and SSL certificate (Feature 05\)  
  * Report the result: the owner is notified when the app is ready or when a deployment fails (Feature 11), and admins can see the deploy result in the Admin Portal (Feature 10\)

## **3.8. Website Management** {#3.8.-website-management}

Website Management is where the business owner looks after a website that is already live. From here, the owner can go back into the editor to change the website, preview the changes, redeploy so that visitors see the latest version, and open the Business Portal to manage the website's products and business information. The Business Portal is part of the WebBlock platform, not of the generated website: it reads and writes the website's data through the generated backend API (Feature 06), and it is available on the web and in the WebBlock mobile app (Android \+ iOS). A business owner can only access the websites and data of their own businesses.

**Roles:** User

**Sub-features**

* **Edit website**  
  * Open the project in the Puck editor and modify its content, layout and design  
  * Save the changes as a draft and continue later  
  * Preview the updated website in desktop, tablet and mobile views (Feature 04\) before redeploying it, and go back to the editor if more changes are needed  
* **Manage products and business information (Business Portal)**  
  * Open the Business Portal of the generated website, on the web or in the WebBlock mobile app (Android \+ iOS), without needing a computer  
  * Add new products, update or delete existing products, and update the business information shown on the website

* **Subdomain settings**  
  * View the website’s current WebBlock subdomain  
  * The subdomain is fixed after it is chosen and cannot be changed (Feature 05\)  
* **Redeploy website**  
  * Redeploy the website to publish the latest saved changes to the live site  
  * The updated version becomes visible to visitors once the redeploy is finished

## **3.9. Payment Service** {#3.9.-payment-service}

The Payment Service controls how business owners pay for using WebBlock. A new owner can publish their first website for free, and it stays online for 14 days. To keep the website online after the trial, the owner subscribes to one of three paid plans (Plus, Pro or Ultra), each with its own monthly price and limits, and pays every month to keep the plan active.

**Roles:** User (business owner). 

**Sub-features**

* **14-day free trial**  
  * **User**: use the free trial for the first deploy, without choosing a paid plan  
  * **User**: the website stays online for 14 days during the trial  
* **Paid plans (Plus, Pro, Ultra)**  
  * **User**: view the paid plans and compare their monthly price and limits  
  * **User**: subscribe to a paid plan to keep the website online after the trial  
  * **Admin**: manage the plans’ monthly price, limits and offers (see Feature 10\)  
* **Monthly payment**  
  * User: pay each month to keep the plan active  
  * User: receive a reminder by mobile and email when the monthly payment is due (Feature 11\)

This service covers WebBlock subscriptions only. The MVP does not provide e-commerce transactions (shopping carts, checkout or payment collection) for a business’s own customers.

## **3.10. Admin Portal (WebBlock Admin)** {#3.10.-admin-portal-(webblock-admin)}

The Admin Portal is WebBlock’s internal back office and is used only by WebBlock system admins. It gives admins an overview of every business owner’s account and every website on the platform. Admins use it to look up a user and their websites, to check whether each website was deployed successfully, to read the deployment logs and monitor each live website, and to fix a problem by redeploying a website when needed. The Admin Portal also holds the Account & Subscription section, which is the admin side of the Payment Service (Feature 09): here admins look after every business owner's subscription, keep the plans (Plus, Pro, Ultra) and their prices and limits up to date, and create offers such as discounts or free trials. Business owners have no access to any part of the Admin Portal.

**Roles:** Admin (WebBlock system admin; WebBlock’s own portal for running the platform)

**Sub-features**

* **Manage user accounts**  
  * View the list of all user accounts and search them by name or email  
  * Open an account to view its details  
  * View the projects (websites) that belong to a user and the status of each website  
* **Manage user websites**  
  * View the list of all websites and search them by owner, domain or status  
  * View the deploy result of each website, to check whether its latest deployment succeeded or failed  
  * Redeploy a website when needed, for example after a failed deployment  
* **Deployment logs & monitoring**  
  * View the deployment logs of each website, to see what happened during a deployment and why it failed  
  * Monitor each live website: whether it is up, whether it has crashed, and how close it is to the resource limits of its plan  
* **Manage user subscriptions (Account & Subscription)**  
  * Search subscriptions by user, project or status  
  * View the details of a subscription: plan, status, start/end date and deploys left  
  * View the free trial status and the number of days left  
  * View the payment history of the subscription plan  
  * Update a user’s subscription plan  
* **Manage plans & pricing (Plus, Pro, Ultra)**  
  * View all plans with their price, limits and active status  
  * Select a plan to edit  
  * Edit the monthly price of a plan (a confirmation is required before the change is saved)  
  * Edit the plan limits: number of sites, redeploys per month, products, and logs/monitoring  
* **Offers**  
  * Create an offer: a discount or a free trial  
  * Set the start date and end date of the offer

The Admin Portal is separate from the business owner's Dashboard and Website Management and must not expose platform-level administration to business users.

## **3.11. Notifications** {#3.11.-notifications}

Notifications keep the website owner informed about what happens to their website, without the owner having to check manually. The system sends them automatically when an important event happens, such as the app becoming ready, a deployment failing, a monthly payment being due, or the live website crashing or going over its maximum resources. Each event is sent through the channels that fit its importance: web and mobile for app and deploy events, and mobile and email for the most important alerts.

**Roles:** System (runs automatically, no user action); notifications are sent to the website owner

**Sub-features**

* **Web notifications**  
  * Shown on the WebBlock site, even when the owner is in another browser tab or app  
  * Let the owner receive alerts before they have installed the mobile app  
  * Sent for app and deploy events: the app is ready, or a deployment fails  
* **Mobile notifications**  
  * Sent to the owner’s mobile app for every event  
* **Email notifications**  
  * Sent only for the most important alerts: payment, crash and resource alerts

*The system sends a notification when:*

| Event | Channel |
| :---- | :---- |
| The app is ready | Web \+ mobile |
| A deployment fails | Web \+ mobile |
| The monthly payment is due | Mobile \+ email |
| The app crashes during live traffic | Mobile \+ email |
| The website is going over its maximum resources | Mobile \+ email |

# **4\. Feature-to-Workflow Mapping** {#4.-feature-to-workflow-mapping}

The following mapping shows where each WebBlock feature participates in the end-to-end product workflow.

| Workflow Stage | Primary Feature | Supporting Features | Purpose |
| :---- | :---- | :---- | :---- |
| 1\. Create Account | Authentication | — | Register with Google or email (OTP verification), or sign in. |
| 2\. Select Template | Dashboard (Template Selection) | Authentication | Browse and search templates, then select one to open the business information modal. |
| 3\. Enter Business Information | Website Creation | Dashboard | Enter business name, type, description, logo, contact details, address and opening hours. |
| 4\. Edit Website | Visual Website Editor (Puck) | Website Creation | Add, remove and reorder predefined blocks, edit content, layout and design, and save drafts. |
| 5\. Preview Website | Visual Website Editor \+ Preview | — | Check desktop, tablet and mobile views; return to the editor if changes are needed. |
| 6\. Choose Subdomain | Domain & SSL Management | Editor \+ Preview | Choose the WebBlock subdomain before the first deployment; SSL is provisioned automatically. |
| 7\. Start Free Trial | Payment Service | Admin Portal (plans & offers) | The first deploy uses the 14-day free trial. |
| 8\. Generate Code | AI Website Generation (System) | Visual Website Editor | Convert the Puck layout into Next.js code, backend code, schema and CRUD API routes. |
| 9\. Deploy Website | Deployment (CI/CD) (System) | AI Website Generation, Domain & SSL Management | Containerize with Docker, provision infrastructure, and deploy the frontend and backend/API. |
| 10\. Notify Owner | Notifications (System) | Deployment (CI/CD) | Tell the owner the app is ready or a deployment failed (web/mobile). |
| 11\. Manage Website | Website Management | Editor, Domain & SSL, Deployment | Edit the site, and deploy the latest changes. |
| 12\. Manage Business Content | Website Management (Business Portal) | — | Manage products and business information on the web or in the mobile app. |
| 13\. Keep Plan Active | Payment Service | Notifications, Admin Portal (plans & offers) | Subscribe to Plus, Pro or Ultra and pay monthly; payment-due alerts go by mobile \+ email. |

