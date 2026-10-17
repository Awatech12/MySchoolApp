from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('register', views.register, name='register'),
    path('register/complete', views.register, name='register_with_picture'),
    path('home', views.home, name='home'),
    path('logout', views.user_logout, name='logout'),



    path('open_notify/<int:pk>', views.open_notify, name='open_notify'),
    path('notify', views.notifications, name='notifications'),
    path('notify/read-all', views.notifications_mark_all_read, name='notifications_mark_all_read'),
    path('follow/<str:username>', views.follow, name='follow'),

    # ── CBT Exam ──────────────────────────────────────────────────
    path('cbt/',                    views.cbt_subjects,       name='cbt_exams'),       # subject picker
    path('cbt/mathematics/',        views.cbt_mathematics,           name='cbt_mathematics'),
    path('cbt/mathematics/topics/', views.cbt_mathematics_topics, name='cbt_mathematics_topics'),  # ← NEW topic selector
    path('cbt/physics/',            views.cbt_physics,        name='cbt_physics'),     # ← OLD physics CBT (kept)
    path('cbt/physics/topics/',     views.cbt_physics_topics, name='cbt_physics_topics'),  # ← NEW topic selector
    path('cbt/english/',            views.cbt_english,        name='cbt_english'),
    path('cbt/chemistry/',          views.cbt_chemistry,      name='cbt_chemistry'),
    path('cbt/submit/',             views.cbt_submit,         name='cbt_submit'),      # score submission

    # ── CBT Examination (real timed test on registered subjects) ──
    path('examination/',                                   views.cbt_examination,          name='cbt_examination'),
    path('examination/start/',                             views.cbt_examination_start,    name='cbt_examination_start'),
    # teacher question bank (staff only)
    path('examination/schedule/',                          views.cbt_exam_schedule,        name='cbt_exam_schedule'),
    path('examination/schedule/<int:pk>/delete/',          views.cbt_exam_schedule_delete, name='cbt_exam_schedule_delete'),
    path('examination/questions/',                         views.cbt_question_bank,        name='cbt_question_bank'),
    path('examination/questions/upload/',                  views.cbt_question_upload,      name='cbt_question_upload'),
    path('examination/questions/add/',                     views.cbt_question_add,         name='cbt_question_add'),
    path('examination/questions/template/',                views.cbt_question_template,    name='cbt_question_template'),
    path('examination/questions/passages/add/',           views.cbt_passage_save,         name='cbt_passage_add'),
    path('examination/questions/passages/<int:pk>/edit/', views.cbt_passage_save,         name='cbt_passage_edit'),
    path('examination/questions/passages/<int:pk>/delete/', views.cbt_passage_delete,     name='cbt_passage_delete'),
    path('examination/questions/<int:pk>/edit/',           views.cbt_question_edit,        name='cbt_question_edit'),
    path('examination/questions/<int:pk>/toggle/',         views.cbt_question_toggle,      name='cbt_question_toggle'),
    path('examination/questions/<int:pk>/delete/',         views.cbt_question_delete,      name='cbt_question_delete'),
    path('examination/<uuid:attempt_id>/',                 views.cbt_examination_take,     name='cbt_examination_take'),
    path('examination/<uuid:attempt_id>/save/',            views.cbt_examination_save,     name='cbt_examination_save'),
    path('examination/<uuid:attempt_id>/submit/',          views.cbt_examination_submit,   name='cbt_examination_submit'),
    path('examination/<uuid:attempt_id>/result/',          views.cbt_examination_result,   name='cbt_examination_result'),
    # ─────────────────────────────────────────────────────────────

    # Live video URLs
    path('live/', views.live_room_list, name='live_room_list'),
    path('live/create/', views.create_live_room, name='create_live_room'),
    path('live/<str:room_name>/', views.live_room, name='live_room'),
    path('live/<str:room_name>/end/', views.end_live_room, name='end_live_room'),
    path('live/<str:room_name>/start/', views.start_live_room, name='start_live_room'),

    # ── Chat Room / Class Discussion URLs ─────────────────────────
    # (Student Q&A Centre)
    path('chat/', views.chat_room, name='chat_room'),
    path('chat/create/', views.create_class_post, name='create_class_post'),
    path('chat/post/<uuid:post_id>/', views.chat_post_detail, name='chat_post_detail'),
    path('chat/post/<uuid:post_id>/edit/', views.edit_class_post, name='edit_class_post'),
    path('chat/post/<uuid:post_id>/delete/', views.delete_class_post, name='delete_class_post'),
    path('chat/post/<uuid:post_id>/comment/', views.chat_post_comment, name='chat_post_comment'),
    path('chat/post/<uuid:post_id>/like/', views.chat_post_like, name='chat_post_like'),
    path('chat/post/<uuid:post_id>/bookmark/', views.bookmark_post, name='bookmark_post'),
    path('chat/post/<uuid:post_id>/resolve/', views.resolve_post, name='resolve_post'),
    path('chat/comment/<uuid:comment_id>/like/', views.chat_comment_like, name='chat_comment_like'),
    path('chat/comment/<uuid:comment_id>/helpful/', views.helpful_vote, name='helpful_vote'),
    path('chat/comment/<uuid:comment_id>/accept/', views.accept_answer, name='accept_answer'),
    path('chat/comment/<uuid:comment_id>/edit/', views.edit_class_comment, name='edit_class_comment'),
    path('chat/comment/<uuid:comment_id>/delete/', views.delete_class_comment, name='delete_class_comment'),
    # kind is 'post' (a question) or 'comment' (an answer)
    path('chat/<str:kind>/<uuid:obj_id>/report/', views.report_content, name='report_content'),
    path('chat/<str:kind>/<uuid:obj_id>/report/clear/', views.clear_reports, name='clear_reports'),

    # ── Admin dashboard (staff only) ──
    path('dashboard/', views.dashboard_home, name='admin_dashboard'),
    path('dashboard/api/students/', views.api_students, name='dash_students'),
    path('dashboard/api/students/create/', views.api_student_create, name='dash_student_create'),
    path('dashboard/api/students/bulk/', views.api_students_bulk, name='dash_students_bulk'),
    path('dashboard/api/students/import/', views.api_students_import, name='dash_students_import'),
    path('dashboard/api/students/export/', views.api_students_export, name='dash_students_export'),
    path('dashboard/api/students/<int:pk>/update/', views.api_student_update, name='dash_student_update'),
    path('dashboard/api/students/<int:pk>/delete/', views.api_student_delete, name='dash_student_delete'),
    path('dashboard/api/students/<int:pk>/toggle/', views.api_student_toggle, name='dash_student_toggle'),
    path('dashboard/api/students/<int:pk>/reset-password/', views.api_student_reset_password, name='dash_student_reset'),
    path('dashboard/api/students/<int:pk>/results/', views.api_student_results, name='dash_student_results'),
    path('dashboard/api/students/<int:pk>/clear-results/', views.api_student_clear_results, name='dash_student_clear'),
    path('dashboard/api/teachers/', views.api_teachers, name='dash_teachers'),
    path('dashboard/api/teachers/create/', views.api_teacher_create, name='dash_teacher_create'),
    path('dashboard/api/teachers/<int:pk>/update/', views.api_teacher_update, name='dash_teacher_update'),
    path('dashboard/api/teachers/<int:pk>/delete/', views.api_teacher_delete, name='dash_teacher_delete'),
    path('dashboard/api/teachers/<int:pk>/toggle/', views.api_teacher_toggle, name='dash_teacher_toggle'),
    path('dashboard/api/teachers/<int:pk>/approve/', views.api_teacher_approve, name='dash_teacher_approve'),
    path('dashboard/api/teachers/<int:pk>/reset-password/', views.api_teacher_reset_password, name='dash_teacher_reset'),
    path('dashboard/api/exams/<uuid:exam_id>/delete/', views.api_exam_delete, name='dash_exam_delete'),
    path('dashboard/api/content/', views.api_content, name='dash_content'),
    path('dashboard/api/content/delete/', views.api_content_delete, name='dash_content_delete'),

    # profile must stay LAST – catches any remaining <str:username>
    path('change-password/', views.change_password, name='change_password'),
    path('update-passport/', views.update_passport, name='update_passport'),
    path('subjects/update/', views.update_subjects, name='update_subjects'),
    path('my-subjects/', views.my_subjects, name='my_subjects'),
    path('results/', views.my_results, name='my_results'),
    path('results/print/<str:username>/', views.print_test_results, name='print_test_results'),
    path('results/<str:username>/', views.my_results, name='my_results_user'),
    path('id-card/', views.id_card, name='id_card'),
    path('id-card/<str:username>/', views.id_card, name='id_card_user'),
    path('<str:username>', views.profile, name='profile'),
]



